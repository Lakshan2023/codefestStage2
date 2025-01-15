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
from datetime import datetime
from pydantic import BaseModel
import uvicorn
from fastapi import FastAPI, HTTPException, status, Depends, responses, security, BackgroundTasks, Form
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uuid
from typing import Dict, List
from datetime import datetime
import plotly
import random
import ast
import plotly.io as pio
import plotly.utils
import plotly.graph_objects as go
import plotly.express as px
from fastapi.staticfiles import StaticFiles

load_dotenv()

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

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

def connect_to_neo4j():
    try:
        driver = GraphDatabase.driver(uri, auth=(username, password))
        driver.verify_connectivity()
        print("Connection to Neo4j established successfully!")
        return driver
    except Exception as e:
        print(f"Failed to connect to Neo4j: {str(e)}")
        return None

driver = connect_to_neo4j()

# Load JSON data
with open('sample.json', 'r') as file:
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

class Question(BaseModel):
    question: str
    session_id: str
    question_history: List[str] = []  # Add this line
    answer_history: List[str] = []  # Add this line

# In-memory storage for chat histories
chat_histories: Dict[str, Dict[str, List[str]]] = {}

app.mount("/static", StaticFiles(directory="static"), name="static")


graph_db_structure = """
// First create Sales Consultant nodes
LOAD CSV WITH HEADERS FROM 'file:///sales_consultants.csv' AS ss
CREATE (:Sales {
    Sales_Consultant_ID: ss.Sales_Consultant_ID,
    Name: ss.Name,
    Email: ss.Email,
    Sysco_Lab_Branch: ss.Sysco_Lab_Branch,
    Address: ss.Address,
    Contact_Number: ss.Contact_Number
})
WITH count(*) as dummy

// Create Customer nodes
LOAD CSV WITH HEADERS FROM 'file:///customers.csv' AS ct
CREATE (:Customer {
    Customer_Name: ct.Customer_Name,
    Region: ct.Region,
    Email: ct.Email,
    Telephone: ct.Telephone,
    Address: ct.Address,
    Customer_ID: ct.Customer_ID,
    Country: ct.Country
})
WITH count(*) as dummy2

// Create Product nodes
LOAD CSV WITH HEADERS FROM 'file:///Sysco_Food_Products.csv' AS fp
CREATE (:Product {
    Product_Name: fp['Product Name'],
    Category: fp.Category,
    Product_ID: fp['Product ID']
})
WITH count(*) as dummy3

// Create Professional Development nodes and relationships
LOAD CSV WITH HEADERS FROM 'file:///modified_professional_development.csv' AS pd
MATCH (s:Sales {Sales_Consultant_ID: pd.Consultant_ID})
CREATE (p:ProfessionalDevelopment {
    Attendance_Status: pd.Attendance_Status,
    Consultant_ID: pd.Consultant_ID,
    Event_ID: pd.Event_ID,
    Event_Type: pd.Event_Type,
    Event_Time: time(pd.Event_Time),
    Event_Date: date(pd.Event_Date)
})
CREATE (s)-[:ATTENDED]->(p)
WITH count(*) as dummy4

// Create Meeting nodes and relationships
LOAD CSV WITH HEADERS FROM 'file:///processed_meetings.csv' AS mt
MATCH (s:Sales {Sales_Consultant_ID: mt.Sales_Consultant_ID})
MATCH (c:Customer {Customer_ID: mt.Customer_ID})
CREATE (m:Meeting {
    Meeting_Time: time(mt.Meeting_Time),
    Meeting_Details: mt.Meeting_Details,
    Meeting_Date: date(mt.Meeting_Date),
    Meeting_ID: mt.Meeting_ID,
    Customer_ID: mt.Customer_ID,
    Sales_Consultant_ID: mt.Sales_Consultant_ID,
    Delivery_Type: mt.Delivery_Type
})
CREATE (s)-[:CONDUCTED]->(m)
CREATE (c)-[:PARTICIPATED_IN]->(m)
WITH count(*) as dummy5

// Create Order nodes and relationships
LOAD CSV WITH HEADERS FROM 'file:///order_data.csv' AS od
MATCH (s:Sales {Sales_Consultant_ID: od.Sales_Consultant_ID})
MATCH (c:Customer {Customer_ID: od.Customer_ID})
MATCH (p:Product {Product_ID: od.Product_ID})
CREATE (o:Order {
    Order_Date: date(od.Order_Date),
    Total_Cost: toFloat(od.Total_Cost),
    Sysco_Operation_Center: od.Sysco_Operation_Center,
    Customer_ID: od.Customer_ID,
    Sales_Consultant_ID: od.Sales_Consultant_ID,
    Order_ID: od.Order_ID,
    Order_Time: time(od.Order_Time),
    Product_ID: od.Product_ID,
    Order_Type: od.Order_Type,
    Order_Status: od.Order_Status,
    Cost_Per_Unit: toFloat(od.Cost_Per_Unit),
    Order_Amount: toInteger(od.Order_Amount)
})
CREATE (s)-[:PROCESSED]->(o)
CREATE (c)-[:PLACED]->(o)
CREATE (o)-[:CONTAINS]->(p)
"""


def generate_query(user_question: str, session_id: str) -> str:
    recent_history = []
    if session_id in chat_histories:
        questions = chat_histories[session_id]["questions"]
        answers = chat_histories[session_id]["answers"]
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

    # Move the prompt template outside the function
    QUERY_CHAT_PROMPT = """Role and Context:
                        You are SyscoPulse, an AI assistant developed by AI-Alchemists from the Informatics Institute of Technology's Artificial Intelligence and Data Science program. Your primary purpose is to help Sales Consultants at Sysco Labs optimize their services and provide comprehensive information about Sysco Labs operations.
                        
                        Recent Conversation History: (This contains the recent questions user asked)
                        -------------------------
                        {history_text}
                        
                        Core Capabilities:
                        ----------------
                        1. Provide comprehensive information about Sysco Labs services and operations
                        2. Assist with sales and customer relationship management
                        3. Help with performance analysis and optimization
                        4. Support meeting and schedule management
                        5. Offer insights on product information and ordering
                        6. Answer general questions about Sysco Labs and its services
                        
                        About Sysco Labs:
                        ----------------
                        Sysco LABS is the technology and innovation arm of Sysco Corporation, the global leader in foodservice distribution. The organization specializes in:
                        - Designing advanced digital solutions for the foodservice industry
                        - Optimizing supply chains through innovative technology
                        - Enhancing customer experiences with modern digital platforms
                        - Driving operational efficiency through automation and analytics
                        - Supporting global foodservice operations
                        - Developing cutting-edge e-commerce solutions
                        - Creating technology solutions for inventory management
                        - Implementing data-driven decision-making tools
                        
                        Database Context:
                        ---------------
                        Current Graph Database Structure: {graph_db_structure}
                        
                        Reference Examples (Not Part of Current Conversation or history):
                        -------------------------
                        Reference Examples : {similar_chunks}
                        
                       ⚠️ Note: These examples are ONLY for cypher query generation reference. Do not use Reference Examples as conversation history or general content.
                        Query Requirements:
                        -----------------
                        1. All queries must start with OPTIONAL MATCH
                        2. Only use nodes, elements, and relationships defined in the provided graph structure
                        3. Support cross-table filtering when necessary
                        
                        Input Question:
                        --------------
                        {question}
                        
                        Response Format:
                        --------------
                        IMPORTANT: Return ONLY the following structure with no prefix text like 'Template:' or 'JSON:':
                         {template_cypher}
                
                        ⚠️ DO NOT include any text before or after the response structure
                        ⚠️ DO NOT add labels like 'Template:', 'Response:', or 'JSON:'
                        ⚠️ DO NOT include any additional formatting or explanation
                        ⚠️ ONLY return the exact structure shown above
                        
                        Response Guidelines:
                        ------------------
                        1. text_explanation:
                           - Provide natural language explanations
                           - Can include emojis for better readability
                           - Must NOT contain code or queries
                           - For general questions, provide complete, knowledgeable answers here
                           - For data-related questions, focus on insights and business impact
                           - Never mention querying or accessing a database
                           - Maintain a conversational, helpful tone
                           - Provide natural language explanations ONLY about Sysco Labs
                           - Must be strictly related to Sysco Labs operations and services
                           - Must NOT contain any political, controversial, or non-Sysco content
                           - For general questions, ONLY provide answers related to Sysco Labs
                           - Maintain a professional, business-focused tone
                        
                        2. query_generation_status:
                           - 'Yes': If a Cypher query can be generated
                           - 'No': If question cannot be answered with a query
                           - If 'No', query field should be empty
                        
                        3. plotly_code_generation_status:
                           - 'Yes': If query results can be visualized in a single line/bar chart
                           - 'No': If data isn't suitable for visualization
                           - Only set to 'Yes' for statistical, numeric, or time series data
                        
                       4. plotly_code:
                           - IMPORTANT: You must use 'response_dict' to access the actual query results
                           - Do NOT use any example/dummy data or hardcoded values
                           - All data for visualization must come from response_dict
                           - response_dict contains the results of your Cypher query as a list of dictionaries
                           - Example correct usage: 
                             df = pd.DataFrame(response_dict)
                             fig = px.line(df, x='date_column', y='value_column')
                           - Provide charts in decent colors (bars and lines should be dark and vibrant colors)
                           - fig.show() code has to be there at the end (This is very very important)
                           - Include visualization code only if plotly_code_generation_status is 'Yes'
                           - Ensure visualizations are business-relevant and actionable
                           - plotly_white is the background layout used. 
                           Color requirements (MUST use different colors for different charts):
                               - Line plots: Use '#1f77b4' (blue) or '#e41a1c' (red) for lines
                               - Bar plots: Use '#2c5282' (deep blue) or '#276749' (green) for bars
                               - Multiple traces: Rotate through colors ['#1f77b4', '#2ca02c', '#9467bd', '#d62728']
                               - NEVER use the same color for different visualizations in the same analysis
                        Example of correct plotly code:
                        ----------------------------
                        # This shows how to properly use response_dict
                        df = pd.DataFrame(response_dict)
                        fig = px.bar(df, 
                                    x=df['date_column'],
                                    y=df['value_column'],
                                    title='Chart Title')
                        
                        # INCORRECT - Do not use:
                        data = ['date': '2024-01', 'value': 100]  # Don't use example data
                        df = pd.DataFrame(data)  # Don't create dummy dataframes
                
                        Important Notes for visualizations:
                        --------------
                        - response_dict will contain the actual query results at runtime
                        - Never generate example data or use hardcoded values
                        - Always read from response_dict for all data visualization
                        
                       Important Notes for whole generation:
                        --------------
                        - If user refers 'you', it means Sysco Labs
                        - Provide natural, informative responses without mentioning data sources
                        - Handle both general inquiries and specific analysis requests seamlessly about sysco labs
                        - Focus on delivering value and insights rather than explaining methodology
                        - Always maintain a professional yet approachable tone (Cannot make any jokes)
                        - For general questions, provide comprehensive answers based on Sysco Labs knowledge
                        - For specific queries, focus on insights and actionable recommendations
                        - Never mention database or data access in responses
                        - You can provide answers only about sysco labs.
                        - Only respond to questions directly related to Sysco Labs
                        - If a question is not about Sysco Labs, politely decline to answer
                        - Do not engage with political, controversial, or non-business topics
                        - Immediately redirect non-Sysco Labs questions back to Sysco Labs topics
                        - You can ONLY provide answers about Sysco Labs
                        - You can refer your knowledge if the questions are not from the graph db about sysco labs general questions.
                        - If you are not capable of providing an answer explain it as well and say you can provide answers only about sysco labs.
                        - For basic questions ("What is", "Who is CEO"):
                          Provide clear, direct answers about Sysco Labs without mentioning data sources
                        
                        - For complex queries:
                          Focus on insights and actionable recommendations
                        
                        # Boundaries 🛡️
                        - Only provide information about Sysco Labs
                        - For non-Sysco Labs questions: "I focus exclusively on Sysco Labs information. Would you like to know about [relevant Sysco Labs topic]? 🎯"
                        - Avoid political, controversial, or non-business topics
                        - If unable to answer: "While I specialize in Sysco Labs information, I don't have enough details to answer this specific question. However, I'd be happy to tell you about [related Sysco Labs topic] 💡"
                        - Professional Emojis is must
                        - If you do long explanations provide answers point wise
"""


    retry_count = 0
    max_retries = 4

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
    formatted_prompt = QUERY_CHAT_PROMPT.format(
        history_text=history_text,
        graph_db_structure=graph_db_structure,
        similar_chunks=similar_text,
        question=user_question,
        template_cypher=TEMPLATE_CYPHER
    )

    while retry_count < max_retries:
        try:
            # Construct chat messages
            chat_messages = [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": formatted_prompt
                        }
                    ]
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": user_question
                        }
                    ]
                }
            ]

            # Make API call
            completion = client.chat.completions.create(
                model=deployment,
                messages=chat_messages,
                max_tokens=4096,
                temperature=0.5,
                top_p=0.95,
                frequency_penalty=0,
                presence_penalty=0,
                stop=None,
                stream=False
            )

            return completion.choices[0].message.content

        except Exception as e:
            retry_count += 1
            if retry_count >= max_retries:
                return f"Error after {max_retries} retries: {str(e)}"
            continue

    return "Maximum retries exceeded"

@app.post("/start_session")
async def start_session():
    session_id = str(uuid.uuid4())
    chat_histories[session_id] = {
        "questions": [],
        "answers": []
    }
    return JSONResponse(content={"session_id": session_id})

def serialize_neo4j_value(val):
    """Serialize Neo4j specific data types"""
    if isinstance(val, (neo4j.time.DateTime, neo4j.time.Date)):
        return val.iso_format()
    elif isinstance(val, neo4j.time.Time):
        return str(val)
    elif isinstance(val, (int, float, str, bool)):
        return val
    elif val is None:
        return None
    return str(val)

def serialize_value(val):
    """Main serialization function for all types"""
    if pd.isna(val):
        return None
    elif isinstance(val, (pd.Timestamp, datetime)):
        return val.isoformat()
    elif isinstance(val, pd.Period):
        return val.strftime('%Y-%m-%d')
    elif isinstance(val, np.integer):
        return int(val)
    elif isinstance(val, np.floating):
        return float(val)
    elif isinstance(val, np.bool_):
        return bool(val)
    elif isinstance(val, (np.ndarray, list)):
        return [serialize_value(v) for v in val]
    elif isinstance(val, dict):
        return {k: serialize_value(v) for k, v in val.items()}
    elif isinstance(val, pd.Index):
        return serialize_value(val.tolist())
    elif isinstance(val, pd.Series):
        return serialize_value(val.tolist())
    elif isinstance(val, pd.DataFrame):
        return serialize_value(val.to_dict('records'))
    elif isinstance(val, pd.DateOffset):
        return str(val)
    elif isinstance(val, pd.Interval):
        return str(val)
    return serialize_neo4j_value(val)

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
                for key, value in dict(record).items():
                    record_dict[key] = serialize_value(value)
                records.append(record_dict)
            return records
    except Exception as e:
        print(f"Error executing query: {str(e)}")
        return None


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
            # Configure the plot for web display
            fig.update_layout(
                template='plotly_white',
                margin=dict(t=50, l=50, r=50, b=50),
                height=500,
                showlegend=True,
                # Add watermark or branding if needed
                annotations=[
                    dict(
                        text="Sysco Labs Analytics",
                        xref="paper",
                        yref="paper",
                        x=0.98,
                        y=0.02,
                        showarrow=False,
                        font=dict(size=10, color="gray"),
                    )
                ]
            )

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
            return plotly_html
        return "<div class='alert alert-warning'>No figure was generated from the plotly code</div>"

    except Exception as e:
        print(f"Plotly Generation Error: {str(e)}")
        return f"<div class='alert alert-danger'>Error generating plot: {str(e)}</div>"

@app.post("/chat")
async def chat(question: Question):
    try:
        session_id = question.session_id
        user_question = question.question
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        static_dir = "static/temp"

        if session_id not in chat_histories:
            return JSONResponse(
                content={"error": "Invalid session ID"},
                status_code=400
            )

        chat_histories[session_id]["questions"].append(user_question)

        # Get the query generation response
        query_response = generate_query(user_question, session_id)

        try:
            try:
                response_dict = json.loads(query_response)
            except json.JSONDecodeError:
                try:
                    response_dict = ast.literal_eval(query_response)
                except (ValueError, SyntaxError):
                    raise ValueError("Could not parse response as JSON or Python dict")

            # Validate response format
            required_fields = [
                "text_explanation",
                "query_generation_status",
                "query",
                "plotly_code_generation_status",
                "plotly_code"
            ]

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
                "html_code_plotly": ""
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
                    table_filename = f'table_{session_id}_{timestamp}.html'
                    file_path = os.path.join(static_dir, table_filename)
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(result["html_table_data"])
                    result[
                        "table_file_url"] = f"https://codefest-backend.azurewebsites.net/static/temp/{table_filename}"

            if response_dict["plotly_code_generation_status"].lower() == "yes" and response_dict["plotly_code"]:
                if result["data"]:  # Only attempt to plot if we have data
                    result["html_code_plotly"] = generate_plotly_html(
                        response_dict["plotly_code"],
                        result["data"]
                    )

                    # Save chart HTML if it exists
                    if result["html_code_plotly"]:
                        chart_filename = f'chart_{session_id}_{timestamp}.html'
                        file_path = os.path.join(static_dir, chart_filename)
                        os.makedirs(os.path.dirname(file_path), exist_ok=True)
                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(result["html_code_plotly"])
                        result[
                            "chart_file_url"] = f"https://codefest-backend.azurewebsites.net/static/temp/{chart_filename}"
                else:
                    result["html_code_plotly"] = "<div class='alert alert-warning'>No data available for plotting</div>"

            chat_histories[session_id]["answers"].append(result["text_explanation"])

            return JSONResponse(content={
                "session_id": session_id,
                "result": result
            })

        except Exception as e:
            return JSONResponse(
                content={
                    "error": f"Error processing response: {str(e)}",
                    "raw_response": query_response
                },
                status_code=500
            )

    except Exception as e:
        print(f"Error in chat endpoint: {str(e)}")
        return JSONResponse(
            content={
                "error": "An error occurred processing your request",
                "detail": str(e)
            },
            status_code=500
        )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

