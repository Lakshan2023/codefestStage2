import neo4j
from neo4j import GraphDatabase, time
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import pandas as pd
import numpy as np
from dotenv import load_dotenv
import os
from openai import AzureOpenAI
import json
from pydantic import BaseModel
from fastapi.responses import JSONResponse
import uuid
from typing import Dict, List
from datetime import datetime
import plotly
import ast
import plotly.io as pio
import plotly.utils
import plotly.graph_objects as go
import plotly.express as px
from fastapi import APIRouter, HTTPException
from pathlib import Path
from routers import prompts

# Create FastAPI instance
router = APIRouter()

# Load environment variables
load_dotenv()

# Get environment variables
endpoint = os.getenv("ENDPOINT_URL", "https://azurevision23.openai.azure.com/")
deployment = os.getenv("DEPLOYMENT_NAME", "gpt-4o")
subscription_key = os.getenv("AZURE_OPENAI_API_KEY")
uri = os.getenv("NEO4J_URI")
username = os.getenv("NEO4J_USERNAME")
password = os.getenv("NEO4J_PASSWORD")

# Initialize Azure OpenAI client with key-based authentication
client = AzureOpenAI(
    azure_endpoint=endpoint,
    api_key=subscription_key,
    api_version="2024-05-01-preview",
)

# Function to connect to Neo4j
def connect_to_neo4j():
    try:
        driver = GraphDatabase.driver(uri, auth=(username, password)) # Connect to the Neo4j database
        driver.verify_connectivity() # Verify the connection
        print("Connection to Neo4j established successfully!")
        return driver
    except Exception as e:
        print(f"Failed to connect to Neo4j: {str(e)}") # Print the error message
        return None

# Connect to Neo4j
driver = connect_to_neo4j()

# Get the current file's directory and set up static path
CURRENT_DIR = Path(__file__).resolve().parent
STATIC_DIR = CURRENT_DIR.parent / "static" / "temp"
os.makedirs(STATIC_DIR, exist_ok=True)

# Load JSON data
with open('routers/sample.json', 'r') as file:
    data = json.load(file)

# Extract questions and their corresponding cypher queries
questions = [item['question'] for item in data['data']]
cypher_queries = [item['cypher_query'] for item in data['data']]


# Function to find the top 5 similar questions
def find_similar_questions(user_query, questions, cypher_queries, top_n=5):
    # Combine user query with the questions
    corpus = questions + [user_query]

    # Compute TF-IDF vectors
    vectorizer = TfidfVectorizer()
    tfidf_matrix = vectorizer.fit_transform(corpus)

    # Compute cosine similarity of user query with questions
    similarities = cosine_similarity(tfidf_matrix[-1], tfidf_matrix[:-1]).flatten()

    # Get the top N similar questions
    similar_indices = similarities.argsort()[-top_n:][::-1]

    # Return the question, cypher_query, and similarity score
    similar_questions = [
        (questions[i], cypher_queries[i], similarities[i]) for i in similar_indices
    ]

    return similar_questions

# Function to generate a Cypher query based on the user question
class Settings(BaseModel):
    language: str
    politeness_level: str
    formality: str
    creativity: float
    response_length: str

#  Add question_history and answer_history fields to the Question model
class Question(BaseModel):
    question: str
    session_id: str
    settings: Settings
    time: str = None
    date: str = None
    question_history: List[str] = []  # Add this line
    answer_history: List[str] = []  # Add this line

# In-memory storage for chat histories
chat_histories: Dict[str, Dict[str, List[str]]] = {}


# Function to generate a Cypher query based on the user question
graph_db_structure = prompts.graph_db_structure

# Function to generate a Cypher query based on the user question
def generate_query(user_question: str, session_id: str, question: Question) -> str:
    global QUERY_CHAT_PROMPT
    recent_history = [] # Initialize an empty list to store recent conversation history
    if session_id in chat_histories: # Check if the session ID exists in the chat histories
        questions = chat_histories[session_id]["questions"] # Get the questions from the chat histories
        answers = chat_histories[session_id]["answers"] # Get the answers from the chat histories
        # Get the last 3 items, excluding the current question
        for i in range(max(len(questions) - 4, 0), len(questions) - 1):
            recent_history.append({
                "question": questions[i],
                "answer": answers[i]
            })

    # Format the conversation history
    history_text = "No previous conversation." if not recent_history else "\n".join([
        f"Previous Question: {item['question']}\n"
        f"Previous Answer: {item['answer']}\n"
        for item in recent_history
    ])

    print(history_text)

    # Define template as a constant
    TEMPLATE_CYPHER = {
        "text_explanation": "",
        "query_generation_status": "Yes/No",
        "query": "OPTIONAL MATCH query here",
        "plotly_code_generation_status": "Yes/No",
        "plotly_code": "plotly code"
    }

    # Find the top 5 similar questions
    settings_prompt = f"""
       Response Settings:
       - Language: {question.settings.language}
       - Politeness Level: {question.settings.politeness_level}
       - Formality: {question.settings.formality}
       - Response Length: {question.settings.response_length}
       - Current date: {question.date}
       - Current time: {question.time}

       Please adjust your response according to these settings:
       1. Use the specified {question.settings.language} language
       2. Maintain {question.settings.politeness_level} politeness level
       3. Keep {question.settings.formality} tone
       5. Provide {question.settings.response_length} response length
       """

    # Move the prompt template outside the function
    Query_chat_prompt = prompts.QUERY_CHAT_PROMPT


    retry_count = 0 # Initialize retry count
    max_retries = 4 # Set maximum number of retries

    # # Get similar questions (assuming this function exists)
    # try:
    #     similar_chunks = find_similar_questions(user_question, questions, cypher_queries)
    # except Exception as e:
    #     return f"Error finding similar questions: {str(e)}"

    try:
        similar_results = find_similar_questions(user_question, questions, cypher_queries)
        similar_text = "\n".join([
            f"Reference Q: {q}\nReference Query: {c}\nSimilarity: {s:.2f}"
            for q, c, s in similar_results
        ])
    except Exception as e:
        similar_text = "No similar examples found."

    # Format the prompt with all required values
    formatted_prompt = Query_chat_prompt.format(
        date=question.date,
        time=question.time,
        history_text=history_text,
        graph_db_structure=graph_db_structure,
        similar_chunks=similar_text,
        question=user_question,
        template_cypher=TEMPLATE_CYPHER,
        settings_prompt=settings_prompt
    )

    # Retry loop for API call
    while retry_count < max_retries:
        try:
            # Construct chat messages
            chat_messages = [
                {
                    "role": "system", # System role for the AI assistant
                    "content": [ # Content of the message
                        {
                            "type": "text",
                        "text": formatted_prompt # Add the formatted prompt to the content
                        }
                    ]
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": user_question # Add the user question to the content
                        }
                    ]
                }
            ]

            # Make API call
            completion = client.chat.completions.create(
                model=deployment,
                messages=chat_messages,
                max_tokens=4096,
                temperature=question.settings.creativity,
                top_p=0.95,
                frequency_penalty=0,
                presence_penalty=0,
                stop=None,
                stream=False
            )

            # Check if the response contains the query generation status
            return completion.choices[0].message.content

        # Retry on API errors
        except Exception as e:
            retry_count += 1
            if retry_count >= max_retries:
                return f"Error after {max_retries} retries: {str(e)}"
            continue
    # Return error message if maximum retries exceeded
    return "Maximum retries exceeded"

# Define the FastAPI router
@router.post("/start_session")
async def start_session():
    session_id = str(uuid.uuid4()) # Generate a unique session ID
    chat_histories[session_id] = {
        "questions": [],
        "answers": []
    }
    return JSONResponse(content={"session_id": session_id}) # Return the session ID as JSON

# Define the FastAPI router
def serialize_neo4j_value(val):
    """Serialize Neo4j specific data types"""
    if isinstance(val, (neo4j.time.DateTime, neo4j.time.Date)): # Check for DateTime or Date objects
        return val.iso_format()
    elif isinstance(val, neo4j.time.Time): # Check for Time objects
        return str(val)
    elif isinstance(val, (int, float, str, bool)): # Check for basic data types
        return val
    elif val is None:
        return None
    return str(val)

# Define the FastAPI router
def serialize_value(val):
    """Main serialization function for all types"""
    if pd.isna(val):
        return None
    # Handle pandas Period objects
    elif isinstance(val, pd.Period):
        return val.asfreq('D').strftime('%Y-%m-%d')
    # Handle both datetime and Timestamp objects
    elif isinstance(val, (pd.Timestamp, datetime)):
        # Convert to ISO format and truncate microseconds if present
        iso_date = val.isoformat()
        if '.' in iso_date:
            iso_date = iso_date.split('.')[0] + 'Z'
        return iso_date
    elif isinstance(val, str):
        # Try to parse string as datetime if it looks like a date
        try:
            if 'T' in val or '-' in val:
                parsed_date = pd.to_datetime(val)
                return parsed_date.strftime('%Y-%m-%dT%H:%M:%SZ')
        except:
            return val
    elif isinstance(val, np.integer): # Handle numpy integer types
        return int(val)
    elif isinstance(val, np.floating): # Handle numpy float types
        return float(val)
    elif isinstance(val, np.bool_): # Handle numpy boolean types
        return bool(val)
    elif isinstance(val, (np.ndarray, list)): # Handle numpy arrays and lists
        return [serialize_value(v) for v in val]
    elif isinstance(val, dict): # Handle dictionaries
        return {k: serialize_value(v) for k, v in val.items()}
    elif isinstance(val, pd.Index): # Handle pandas Index objects
        return serialize_value(val.tolist())
    elif isinstance(val, pd.Series): # Handle pandas Series objects
        return serialize_value(val.tolist())
    elif isinstance(val, pd.DataFrame): # Handle pandas DataFrame objects
        return serialize_value(val.to_dict('records'))
    elif isinstance(val, pd.DateOffset): # Handle pandas DateOffset objects
        return str(val)
    elif isinstance(val, pd.Interval): # Handle pandas Interval objects
        return str(val)
    return serialize_neo4j_value(val) # Fallback to Neo4j serialization

# Define the FastAPI router
def execute_cypher_query(driver, query):
    """Execute a Cypher query and return serialized results"""
    try:
        with driver.session() as session:
            result = session.run(query)
            # Convert Neo4j records to dictionaries and serialize the values
            records = []
            for record in result:
                # Convert each record to a dict and serialize all values
                record_dict = {}
                for key, value in dict(record).items(): # Convert to dict to access items
                    record_dict[key] = serialize_value(value)
                records.append(record_dict) # Append the serialized record to the list
            return records
    except Exception as e: # Handle any exceptions
        print(f"Error executing query: {str(e)}")
        return None

# Function to generate a Cypher query based on the user question
def convert_to_html_table(data):
    if not data:
        return ""

    # Convert data to pandas DataFrame
    df = pd.DataFrame(data)

    # Function to convert email to clickable link
    def make_email_link(value):
        if pd.isna(value):
            return ""
        value_str = str(value).strip()
        # Check if the value looks like an email address
        if '@' in value_str and '.' in value_str.split('@')[1]:
            return f'<a href="mailto:{value_str}" style="color: #0066cc; text-decoration: none;">{value_str}</a>'
        return value_str

    # Identify email columns (any column with 'email' in the name, case-insensitive)
    email_columns = [col for col in df.columns if 'email' in col.lower()]

    # Apply the email link conversion to identified columns
    for col in email_columns:
        df[col] = df[col].apply(make_email_link)

    # Generate HTML table
    html_table = df.to_html(
        classes=['table', 'table-striped', 'table-hover', 'table-bordered'],
        index=False,
        escape=False,
        table_id='result-table'
    )

    # Add CSS to make column separators visible and apply additional styling
    html_table = html_table.replace('<table',
        '<table style="border-collapse: collapse; border: 1px solid black; width: 100%;"')
    html_table = html_table.replace('<th',
        '<th style="border: 1px solid black; text-align: center; padding: 8px;"')
    html_table = html_table.replace('<td',
        '<td style="border: 1px solid black; text-align: right; padding: 8px;"')

    return html_table

# Function to generate a Cypher query based on the user question
def generate_plotly_html(plotly_code, response_dict):
    """
    Generate HTML for plotly visualization with proper error handling and data validation
    """
    try:
        # Validate response_dict
        if not response_dict or not isinstance(response_dict, list):
            return "<div class='alert alert-warning'>No valid data available for plotting</div>"

        # Create namespace with all required libraries and data
        namespace = {
            'pd': pd,
            'np': np,
            'go': go,
            'px': px,
            'plotly': plotly,
            'response_dict': response_dict,
        }

        # Add data validation before execution
        if 'df = pd.DataFrame(response_dict)' not in plotly_code:
            plotly_code = f"df = pd.DataFrame(response_dict)\n{plotly_code}"

        # Execute the plotly code in the prepared namespace
        exec(plotly_code, namespace)

        # Retrieve the figure from namespace
        fig = namespace.get('fig')

        if fig:
            # Configure the plot for web display with proper background settings
            fig.update_layout(
                template='plotly_white',
                paper_bgcolor='white',
                plot_bgcolor='white',
                margin=dict(t=50, l=50, r=50, b=50),
                autosize=True,  # Enables automatic resizing
                showlegend=True,
                annotations=[
                    dict(
                        text="SyscoPulse Analytics",
                        xref="paper",
                        yref="paper",
                        x=0.98,
                        y=0.02,
                        showarrow=False,
                        font=dict(size=10, color="gray"),
                    )
                ]
            )

            # Make the figure responsive
            fig.update_layout(
                # Remove fixed height/width to allow responsiveness
                height=None,
                width=None
            )

            # Additional styling for better visualization
            fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor='lightgray')
            fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor='lightgray')

            # Convert to HTML with specific configurations
            plotly_html = pio.to_html(
                fig,
                full_html=False,
                include_plotlyjs=True,
                include_mathjax=False,
                config={
                    'responsive': True,
                    'displayModeBar': True,
                    'modeBarButtonsToRemove': ['select2d', 'lasso2d']
                }
            )
            # Return the generated HTML for the plotly visualization
            return plotly_html # Return the generated HTML for the plotly visualization
        return "<div class='alert alert-warning'>No figure was generated from the plotly code</div>"

    # Handle exceptions during plotly generation
    except Exception as e:
        print(f"Plotly Generation Error: {str(e)}")
        return f"<div class='alert alert-danger'>Error generating plot: {str(e)}</div>"


# Function to generate a Cypher query based on the user question
def generate_chart_analysis(plotly_code, data, user_question, client, deployment, question):
    if not plotly_code or not data: # Check if plotly code or data is missing
        return "Unable to provide analytical insights at the moment due to missing data or visualization."

    try:
        # Extract key information from plotly code to understand chart type
        plotly_code_lower = plotly_code.lower()
        chart_type = ""
        if "px.bar" in plotly_code_lower: # Check if the plotly code is for a bar chart
            chart_type = "bar chart"
        elif "px.line" in plotly_code_lower: # Check if the plotly code is for a line chart
            chart_type = "line chart"
        elif "px.pie" in plotly_code_lower: # Check if the plotly code is for a pie chart
            chart_type = "pie chart"
        else:
            chart_type = "chart" # Default to generic chart type

        settings_prompt = prompts.SETTINGS_PROMPT

        settings_prompt.format(language=question.settings.language, politeness_level=question.settings.politeness_level,
                                  formality=question.settings.formality, response_length=question.settings.response_length,
                                  time=question.time, date=question.date)

        # Prepare prompt for LLM
        analysis_prompt = f"""You are an analytics expert at Sysco Labs. Analyze the following data visualization in context of the user's question and provide 3-4 key business insights.

        Original Question: {user_question}
        Chart Type: {chart_type}
        Data: {str(data)}
        Visualization Code: {plotly_code}

        Requirements:
        1. Address the user's original question directly in your analysis
        2. Focus on business implications and actionable insights
        3. Be specific about trends, patterns, or anomalies
        4. Relate insights to Sysco Labs business context
        5. Keep explanations clear and concise
        6. Avoid technical jargon
        7. Use bullet points for better readability
        8. Include relevant emojis for each point
        9. Ensure insights are directly relevant to what the user wanted to know

        Format your response as a direct list of insights without any preamble or introduction.
        
        Please strictly follow this settings:
            {settings_prompt}
        """

        # Make API call
        completion = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": analysis_prompt},
                {"role": "user", "content": "Please provide the analysis."}
            ],
            max_tokens=1000, # Set maximum tokens for response
            temperature= question.settings.creativity # Set the creativity level based on user settings
        )

        return completion.choices[0].message.content # Return the generated analysis

    except Exception as e: # Handle any exceptions
        return f"Unable to provide analytical insights at the moment. Error: {str(e)}"

# Function to generate a Cypher query based on the user question
def generate_plot_analysis(data: str):
    try:
        # Prepare prompt for LLM
        analysis_prompt = f"""
                If this data contains not only null values return 'yes'.
                If the data only contains null values say you cannot generate an answer with data table at the moment in a creative way with emojis bit longer explanation.
                Don't ever say, since data contains mostly null values or zeros you can't provide an answer.
                
                example data: 
                {{
                "OrderID": null,
                "Product": null,
                "Date": null,
                "TotalCost": null
                }} # If it is like this also say you cannot generate an answer at this moment bit longer ways.
                
                data : {data}
                """

        # Make API call
        completion = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": analysis_prompt},
                {"role": "user", "content": "Please return 'yes' or explanation based on data."}
            ],
            max_tokens=1000, # Set maximum tokens for response
            temperature=0.7 # Set the creativity level
        )

        return completion.choices[0].message.content # Return the generated analysis
    except Exception as e:
        return f"Unable to provide table insights at this moment. Error: {str(e)}"

# Function to generate a Cypher query based on the user question
def explanation_handler(explanation: str):
    try:
        # Prepare prompt for LLM
        examples = """
          Here are the customers who have placed orders with a total value exceeding $10,000.
          
          Here is the analysis of average order value for each product. This visualization helps in identifying which products contribute the most to the revenue.
          
          Here is the analysis of future meetings conducted by sales consultants. This will help in understanding the upcoming engagements and planning accordingly. 📅
        """
        # Prepare prompt for LLM
        analysis_prompt = f"""
                You are SyscoPulse.
                If the below text explanation includes the meaning or terms like, this chert/plot/table analyses,
                Examples: {examples}
                then please provide a statement that, you can't generate a plot at this moment.(Important :Use  professional emojis and use a bit longer text to explain it.) 
                
                If the text explanation is a general explanation, return the same answer as it is.
                very very important: do not provide any other text.
                
                Text Explanation : {explanation}
                """
        # Make API call
        completion = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": analysis_prompt},
                {"role": "user", "content": "Please do what has given in the system prompt properly."}
            ],
            max_tokens=1000, # Set maximum tokens for response
            temperature=0 # Set the creativity level
        )

        return completion.choices[0].message.content # Return the generated analysis
    except Exception as e:
        return f"Unable to provide table insights at this moment. Error: {str(e)}" # Return error message if exception occurs


# Define the FastAPI router
@router.post("/chat")
async def chat(question: Question):
    try:
        # Extract the required information from the question object
        session_id = question.session_id # Extract the session ID
        user_question = question.question # Extract the user question
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S") # Extract the current timestamp

        # Check if the session ID is valid
        if session_id not in chat_histories:
            return JSONResponse(
                content={"error": "Invalid session ID"},
                status_code=400
            )

        # Add the user question to the chat history
        chat_histories[session_id]["questions"].append(user_question)

        # Get the query generation response
        query_response = generate_query(user_question, session_id, question)

        try:
            try:
                # Parse the response as JSON
                response_dict = json.loads(query_response)
            except json.JSONDecodeError:
                try:
                    # Try to parse the response as a Python dict
                    response_dict = ast.literal_eval(query_response)
                except (ValueError, SyntaxError):
                    raise ValueError("Could not parse response as JSON or Python dict") # Raise an error if parsing fails

            # Validate response format
            required_fields = [
                "text_explanation",
                "query_generation_status",
                "query",
                "plotly_code_generation_status",
                "plotly_code"
            ]

            # Check if the response dictionary contains all required fields
            if not isinstance(response_dict, dict) or not all(
                    key in response_dict for key in required_fields):
                raise ValueError("Invalid response format")

            # Initialize the result
            result = {
                "text_explanation": response_dict["text_explanation"],
                "query_generation_status": response_dict["query_generation_status"],
                "query": response_dict["query"],
                "plotly_code_generation_status": response_dict["plotly_code_generation_status"],
                "plotly_code": response_dict["plotly_code"],
                "data": None,
                "html_table_data": "",
                "html_code_plotly": "",
                "chart_analysis": ""
            }

            # Execute the query if status is "Yes"
            if response_dict["query_generation_status"].lower() == "yes" and response_dict["query"]:
                query_result = execute_cypher_query(driver, response_dict["query"])
                # Ensure the entire result is JSON serializable
                result["data"] = json.loads(json.dumps(query_result, default=str))

                # Generate HTML table from the data
                result["html_table_data"] = convert_to_html_table(query_result)

                # Save HTML table if it exists
                if result["html_table_data"]:
                    try:
                        table_filename = f'table_{session_id}_{timestamp}.html' # Generate a unique filename
                        file_path = STATIC_DIR / table_filename # Set the file path

                        # Write the file
                        with open(file_path, 'w', encoding='utf-8') as f: # Open the file in write mode
                            f.write(result["html_table_data"]) # Write the HTML table data to the file

                        # Set the correct URL using the base URL from environment
                        base_url = os.getenv('API_BASE_URL', 'https://codefest-backend.azurewebsites.net')
                        result["table_file_url"] = f"{base_url}/static/temp/{table_filename}"

                    except Exception as e: # Handle any exceptions
                        print(f"Error saving table file: {str(e)}")

                if result["html_table_data"]: # Check if the HTML table data exists
                    result["table_accept_status"] = generate_plot_analysis(result["data"][:300])

            # Generate Plotly visualization if status is "Yes"
            if response_dict["plotly_code_generation_status"].lower() == "yes" and response_dict["plotly_code"]:
                if result["data"]:  # Only attempt to plot if we have data
                    result["html_code_plotly"] = generate_plotly_html(
                        response_dict["plotly_code"], # Extract the plotly code
                        result["data"] # Extract the data
                    )

                    # Save chart HTML if it exists
                    if result["html_code_plotly"]:
                        try:
                            chart_filename = f'chart_{session_id}_{timestamp}.html'
                            file_path = STATIC_DIR / chart_filename

                            # Write the file
                            with open(file_path, 'w', encoding='utf-8') as f:
                                f.write(result["html_code_plotly"])

                            # Set the correct URL using the base URL from environment
                            base_url = os.getenv('API_BASE_URL', 'https://codefest-backend.azurewebsites.net')
                            result["chart_file_url"] = f"{base_url}/static/temp/{chart_filename}"
                        # Handle any exceptions
                        except Exception as e:
                            print(f"Error saving chart file: {str(e)}")
                else: # If no data is available, set an alert message
                    result["html_code_plotly"] = "<div class='alert alert-warning'>No data available for plotting</div>"

            # Generate chart analysis if plotly code was generated successfully
            if result["html_code_plotly"] and "alert alert-warning" not in result["html_code_plotly"].lower(): # Check if plotly code exists
                result["chart_analysis"] = generate_chart_analysis( # Generate chart analysis
                    response_dict["plotly_code"], # Extract the plotly code
                    result["data"][:100], # Extract the data
                    user_question,  # Pass the user's question
                    client,
                    deployment,
                    question
                )
            else:
                result["chart_analysis"] = "Unable to provide analytical insights due to visualization error." # Set error message

            chat_histories[session_id]["answers"].append(result["text_explanation"]) # Append the text explanation to chat history

            if result["data"] is None:
                result["text_explanation"] = explanation_handler(result["text_explanation"]) # Handle explanation based on data availability

            # Return the response as a JSON object
            return JSONResponse(content={
                "session_id": session_id,
                "result": result
            })
        # Handle any exceptions
        except Exception as e:
            return JSONResponse(
                content={
                    "error": f"Error processing response: {str(e)}",
                    "raw_response": query_response
                },
                status_code=500
            )
    # Handle any exceptions
    except Exception as e:
        print(f"Error in chat endpoint: {str(e)}")
        return JSONResponse(
            content={
                "error": "An error occurred processing your request",
                "detail": str(e)
            },
            status_code=500
        )
