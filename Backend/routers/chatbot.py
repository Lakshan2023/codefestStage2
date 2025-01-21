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
from fastapi import APIRouter, HTTPException
from pathlib import Path

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
graph_db_structure = """
:param {
  // Define the file path root and the individual file names required for loading.
  // https://neo4j.com/docs/operations-manual/current/configuration/file-locations/
  file_path_root: 'file:///', // Change this to the folder your script can access the files at.
  file_0: 'sales_consultants.csv',
  file_1: 'customers.csv',
  file_2: 'Sysco_Food_Products.csv',
  file_3: 'modified_professional_development.csv',
  file_4: 'processed_meetings.csv',
  file_5: 'order_data.csv',
  file_6: 'food_vendor_data.csv',
  file_7: 'stock_data.csv',
  file_8: 'vendor_sales_consultant_mapping.csv',
  file_9: 'vendor_product_relationships.csv'
};

// CONSTRAINT creation
// -------------------
//
// Create node uniqueness constraints, ensuring no duplicates for the given node label and ID property exist in the database. This also ensures no duplicates are introduced in future.
//
// NOTE: The following constraint creation syntax is generated based on the current connected database version 5.26-aura.
CREATE CONSTRAINT Sales_Consultant_ID_Sales_uniq IF NOT EXISTS
FOR (n: Sales)
REQUIRE (n.Sales_Consultant_ID) IS UNIQUE;
CREATE CONSTRAINT Customer_ID_Customer_uniq IF NOT EXISTS
FOR (n: Customer)
REQUIRE (n.Customer_ID) IS UNIQUE;
CREATE CONSTRAINT Product_ID_Product__uniq IF NOT EXISTS
FOR (n: `Product `)
REQUIRE (n.Product_ID) IS UNIQUE;
CREATE CONSTRAINT Event_ID_ProfessionalDevelopment_uniq IF NOT EXISTS
FOR (n: ProfessionalDevelopment)
REQUIRE (n.Event_ID) IS UNIQUE;
CREATE CONSTRAINT Meeting_ID_Meeting__uniq IF NOT EXISTS
FOR (n: `Meeting `)
REQUIRE (n.Meeting_ID) IS UNIQUE;
CREATE CONSTRAINT Order_ID_Order_uniq IF NOT EXISTS
FOR (n: Order)
REQUIRE (n.Order_ID) IS UNIQUE;
CREATE CONSTRAINT Vendor_ID_Vendor_uniq IF NOT EXISTS
FOR (n: Vendor)
REQUIRE (n.Vendor_ID) IS UNIQUE;
CREATE CONSTRAINT Stock_ID_Stock_uniq IF NOT EXISTS
FOR (n: Stock)
REQUIRE (n.Stock_ID) IS UNIQUE;

:param {
  idsToSkip: []
};

// NODE load
// ---------
//
// Load nodes in batches, one node label at a time. Nodes will be created using a MERGE statement to ensure a node with the same label and ID property remains unique. Pre-existing nodes found by a MERGE statement will have their other properties set to the latest values encountered in a load file.
//
// NOTE: Any nodes with IDs in the 'idsToSkip' list parameter will not be loaded.
LOAD CSV WITH HEADERS FROM ($file_path_root + $file_0) AS row
WITH row
WHERE NOT row.Sales_Consultant_ID IN $idsToSkip AND NOT row.Sales_Consultant_ID IS NULL
CALL {
  WITH row
  MERGE (n: Sales { Sales_Consultant_ID: row.Sales_Consultant_ID })
  SET n.Sales_Consultant_ID = row.Sales_Consultant_ID
  SET n.Name = row.Name
  SET n.Address = row.Address
  SET n.Email = row.Email
  SET n.Contact_Number = row.Contact_Number
  SET n.Sysco_Lab_Branch = row.Sysco_Lab_Branch
} IN TRANSACTIONS OF 10000 ROWS;

LOAD CSV WITH HEADERS FROM ($file_path_root + $file_1) AS row
WITH row
WHERE NOT row.Customer_ID IN $idsToSkip AND NOT row.Customer_ID IS NULL
CALL {
  WITH row
  MERGE (n: Customer { Customer_ID: row.Customer_ID })
  SET n.Customer_ID = row.Customer_ID
  SET n.Customer_Name = row.Customer_Name
  SET n.Company_Name = row.Company_Name
  SET n.Company_Type = row.Company_Type
  SET n.Email = row.Email
  SET n.Address = row.Address
  SET n.Telephone = row.Telephone
  SET n.City = row.City
  SET n.Country = row.Country
  SET n.Region = row.Region
} IN TRANSACTIONS OF 10000 ROWS;

LOAD CSV WITH HEADERS FROM ($file_path_root + $file_2) AS row
WITH row
WHERE NOT row.Product ID IN $idsToSkip AND NOT row.Product ID IS NULL
CALL {
  WITH row
  MERGE (n: `Product ` { Product_ID: row.Product ID })
  SET n.Product_ID = row.Product ID
  SET n.Product_Name = row.Product Name
  SET n.Category = row.Category
} IN TRANSACTIONS OF 10000 ROWS;

LOAD CSV WITH HEADERS FROM ($file_path_root + $file_3) AS row
WITH row
WHERE NOT row.Event_ID IN $idsToSkip AND NOT row.Event_ID IS NULL
CALL {
  WITH row
  MERGE (n: ProfessionalDevelopment { Event_ID: row.Event_ID })
  SET n.Event_ID = row.Event_ID
  // Your script contains the datetime datatype. Our app attempts to convert dates to ISO 8601 date format before passing them to the Cypher function.
  // This conversion cannot be done in a Cypher script load. Please ensure that your CSV file columns are in ISO 8601 date format to ensure equivalent loads.
  SET n.Event_Date = datetime(row.Event_Date)
  SET n.Event_Time = row.Event_Time
  SET n.Event_Type = row.Event_Type
  SET n.Attendance_Status = row.Attendance_Status
} IN TRANSACTIONS OF 10000 ROWS;

LOAD CSV WITH HEADERS FROM ($file_path_root + $file_4) AS row
WITH row
WHERE NOT row.Meeting_ID IN $idsToSkip AND NOT row.Meeting_ID IS NULL
CALL {
  WITH row
  MERGE (n: `Meeting ` { Meeting_ID: row.Meeting_ID })
  SET n.Meeting_ID = row.Meeting_ID
  // Your script contains the datetime datatype. Our app attempts to convert dates to ISO 8601 date format before passing them to the Cypher function.
  // This conversion cannot be done in a Cypher script load. Please ensure that your CSV file columns are in ISO 8601 date format to ensure equivalent loads.
  SET n.Meeting_Date = datetime(row.Meeting_Date)
  SET n.Meeting_Time = row.Meeting_Time
  SET n.Meeting_Details = row.Meeting_Details
  SET n.Delivery_Type = row.Delivery_Type
} IN TRANSACTIONS OF 10000 ROWS;

LOAD CSV WITH HEADERS FROM ($file_path_root + $file_5) AS row
WITH row
WHERE NOT row.Order_ID IN $idsToSkip AND NOT row.Order_ID IS NULL
CALL {
  WITH row
  MERGE (n: Order { Order_ID: row.Order_ID })
  SET n.Order_ID = row.Order_ID
  SET n.Order_Amount = toInteger(trim(row.Order_Amount))
  SET n.Order_Status = row.Order_Status
  // Your script contains the datetime datatype. Our app attempts to convert dates to ISO 8601 date format before passing them to the Cypher function.
  // This conversion cannot be done in a Cypher script load. Please ensure that your CSV file columns are in ISO 8601 date format to ensure equivalent loads.
  SET n.Order_Date = datetime(row.Order_Date)
  SET n.Sysco_Operation_Center = row.Sysco_Operation_Center
  SET n.Order_Type = row.Order_Type
  SET n.Order_Time = row.Order_Time
  SET n.Cost_Per_Unit = toFloat(trim(row.Cost_Per_Unit))
  SET n.Total_Cost = toFloat(trim(row.Total_Cost))
} IN TRANSACTIONS OF 10000 ROWS;

LOAD CSV WITH HEADERS FROM ($file_path_root + $file_6) AS row
WITH row
WHERE NOT row.Vendor_ID IN $idsToSkip AND NOT row.Vendor_ID IS NULL
CALL {
  WITH row
  MERGE (n: Vendor { Vendor_ID: row.Vendor_ID })
  SET n.Vendor_ID = row.Vendor_ID
  SET n.Vendor_Name = row.Vendor_Name
  SET n.Representative_Name = row.Representative_Name
  SET n.Email = row.Email
  SET n.Telephone = row.Telephone
  SET n.Address = row.Address
  SET n.Country = row.Country
  SET n.Region = row.Region
} IN TRANSACTIONS OF 10000 ROWS;

LOAD CSV WITH HEADERS FROM ($file_path_root + $file_7) AS row
WITH row
WHERE NOT row.Stock_ID IN $idsToSkip AND NOT row.Stock_ID IS NULL
CALL {
  WITH row
  MERGE (n: Stock { Stock_ID: row.Stock_ID })
  SET n.Stock_ID = row.Stock_ID
  // Your script contains the datetime datatype. Our app attempts to convert dates to ISO 8601 date format before passing them to the Cypher function.
  // This conversion cannot be done in a Cypher script load. Please ensure that your CSV file columns are in ISO 8601 date format to ensure equivalent loads.
  SET n.Date = datetime(row.Date)
  SET n.Time = row.Time
  SET n.Stock_Amount = toFloat(trim(row.Stock_Amount))
  SET n.Unit = row.Unit
} IN TRANSACTIONS OF 10000 ROWS;


// RELATIONSHIP load
// -----------------
//
// Load relationships in batches, one relationship type at a time. Relationships are created using a MERGE statement, meaning only one relationship of a given type will ever be created between a pair of nodes.
LOAD CSV WITH HEADERS FROM ($file_path_root + $file_3) AS row
WITH row 
CALL {
  WITH row
  MATCH (source: Sales { Sales_Consultant_ID: row.Consultant_ID })
  MATCH (target: ProfessionalDevelopment { Event_ID: row.Event_ID })
  MERGE (source)-[r: ATTENDED]->(target)
} IN TRANSACTIONS OF 10000 ROWS;

LOAD CSV WITH HEADERS FROM ($file_path_root + $file_4) AS row
WITH row 
CALL {
  WITH row
  MATCH (source: Sales { Sales_Consultant_ID: row.Sales_Consultant_ID })
  MATCH (target: `Meeting ` { Meeting_ID: row.Meeting_ID })
  MERGE (source)-[r: CONDUCTED]->(target)
} IN TRANSACTIONS OF 10000 ROWS;

LOAD CSV WITH HEADERS FROM ($file_path_root + $file_4) AS row
WITH row 
CALL {
  WITH row
  MATCH (source: Customer { Customer_ID: row.Customer_ID })
  MATCH (target: `Meeting ` { Meeting_ID: row.Meeting_ID })
  MERGE (source)-[r: PARTICIPATED_IN]->(target)
} IN TRANSACTIONS OF 10000 ROWS;

LOAD CSV WITH HEADERS FROM ($file_path_root + $file_5) AS row
WITH row 
CALL {
  WITH row
  MATCH (source: Sales { Sales_Consultant_ID: row.Sales_Consultant_ID })
  MATCH (target: Order { Order_ID: row.Order_ID })
  MERGE (source)-[r: PROCESSED]->(target)
} IN TRANSACTIONS OF 10000 ROWS;

LOAD CSV WITH HEADERS FROM ($file_path_root + $file_5) AS row
WITH row 
CALL {
  WITH row
  MATCH (source: Customer { Customer_ID: row.Customer_ID })
  MATCH (target: Order { Order_ID: row.Order_ID })
  MERGE (source)-[r: PLACED]->(target)
} IN TRANSACTIONS OF 10000 ROWS;

LOAD CSV WITH HEADERS FROM ($file_path_root + $file_5) AS row
WITH row 
CALL {
  WITH row
  MATCH (source: Order { Order_ID: row.Order_ID })
  MATCH (target: `Product ` { Product_ID: row.Product_ID })
  MERGE (source)-[r: CONTAINS]->(target)
} IN TRANSACTIONS OF 10000 ROWS;

LOAD CSV WITH HEADERS FROM ($file_path_root + $file_8) AS row
WITH row 
CALL {
  WITH row
  MATCH (source: Sales { Sales_Consultant_ID: row.Sales_Consultant_ID })
  MATCH (target: Vendor { Vendor_ID: row.Vendor_ID })
  MERGE (source)-[r: MANAGES]->(target)
} IN TRANSACTIONS OF 10000 ROWS;

LOAD CSV WITH HEADERS FROM ($file_path_root + $file_9) AS row
WITH row 
CALL {
  WITH row
  MATCH (source: Vendor { Vendor_ID: row.Vendor_ID })
  MATCH (target: `Product ` { Product_ID: row.Food_ID })
  MERGE (source)-[r: SUPPLIES]->(target)
} IN TRANSACTIONS OF 10000 ROWS;

LOAD CSV WITH HEADERS FROM ($file_path_root + $file_7) AS row
WITH row 
CALL {
  WITH row
  MATCH (source: `Product ` { Product_ID: row.Food_ID })
  MATCH (target: Stock { Stock_ID: row.Stock_ID })
  MERGE (source)-[r: HAS_STOCK]->(target)
} IN TRANSACTIONS OF 10000 ROWS;


Naming conventions should use for Nodes when generating Queries (Use Only these Nodes for Queries):
__________________________________________________________________________________________________

Customer

Meeting

Order

Product

ProfessionalDevelopment

Sales

Stock

Vendor

Naming conventions should use for Relationships when generating Queries (Use Only these relationships for Queries):
__________________________________________________________________________________________________________________

ATTENDED

CONDUCTED

CONTAINS

HAS_STOCK

MANAGES

PARTICIPATED_IN

PLACED

PROCESSED

SUPPLIES


Other than that do not use any relationship or Nodes.
  Very very important when you do date comparisons, please use this format for dates: datetime(\"2025-01-18T00:00:00Z\")
  Times are used as strings like this: \"14:23:45\"
"""

# Function to generate a Cypher query based on the user question
def generate_query(user_question: str, session_id: str, question: Question) -> str:
    recent_history = [] # Initialize an empty list to store recent conversation history
    if session_id in chat_histories: # Check if the session ID exists in the chat histories
        questions = chat_histories[session_id]["questions"] # Get the questions from the chat histories
        answers = chat_histories[session_id]["answers"] # Get the answers from the chat histories
        # Get the last 5 items, excluding the current question
        for i in range(max(len(questions) - 6, 0), len(questions) - 1):
            recent_history.append({
                "question": questions[i],
                "answer": str(answers[i])
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
    QUERY_CHAT_PROMPT = """Role and Context:
                        You are SyscoPulse, an AI assistant developed by AI-Alchemists from the Informatics Institute of Technology's Artificial Intelligence and Data Science program. Your primary purpose is to help Sales Consultants at Sysco Labs optimize their services and provide comprehensive information about Sysco Labs operations.
                        
                        Recent Conversation History: (This contains the recent questions user asked)
                        -------------------------
                        {history_text}  (Make sure that last question is given by user is at top of the history list).
                        
                        Core Capabilities:
                        ----------------
                        1. Provide comprehensive information about Sysco Labs services and operations
                        2. Assist with sales and customer relationship management
                        3. Help with performance analysis and optimization
                        4. Support meeting and schedule management
                        5. Offer insights on product information and ordering
                        6. Answer general questions about Sysco Labs and its services
                        ⚠️⚠️ If there is no prior history always welcome the user to SyscoPulse (Do not say who have developed you at that time)
                        
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
                       Very Very Important: ⚠️⚠️ Do Not ever use the same reference examples to generate your answer, because it is not the answer user is expecting. it is given to get an idea of the cypher query structure.
                        Query Requirements:
                        -----------------
                        1. All queries must start with OPTIONAL MATCH
                        2. Only use nodes, elements, and relationships defined in the provided graph structure
                        3. Support cross-table filtering when necessary
                        
                        Today Date:
                        ---------------
                        {date}
                        
                        Today Time:
                        ______________
                        {time}
                        
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
                        
                        3. query structure details:
                           - ⚠️ Do not ever provide multiple relations in one line
                              eg multiple relations: OPTIONAL MATCH (s:Sales {{Sales_Consultant_ID: \"SC0001\"}})-[:CONDUCTED]->(m:Meeting)-[:PARTICIPATED_IN]->(c:Customer)
                              ⚠️ ⚠️  break it down to lines using 'WITH' term.
                           - ⚠️⚠️ If you consider today's date and time, please provide it in the cypher query related date and time formats
                           - ⚠️⚠️ All the cypher people, food and place names are capitalized in the first letter of each word use this method when searching.
                           - Dates are stored as strings: "2024-12-09T00:00:00Z" time is also as strings: "09:00:00"
                           - Never provide multiple relationships together using one OPTIONAL MATCH statement, use multiple OPTIONAL statements always
                        
                        4. plotly_code_generation_status:
                           - 'Yes': If query results can be visualized in a single line/bar chart
                           - 'No': If data isn't suitable for visualization
                           - Only set to 'Yes' for statistical, numeric, or time series data
                        
                       Plotly Visualization Code Generation Guidelines
                        ⚠️ CRITICAL DATA SOURCE RULES ⚠️
                        
                        NEVER modify or assign to response_dict:
                        pythonCopy# ABSOLUTELY WRONG - NEVER DO THIS:
                        response_dict = [
                            {{'date': '2024-01', 'value': 100}},
                            {{'date': '2024-02', 'value': 200}}
                        ]
                        
                        # ABSOLUTELY WRONG - NEVER DO THIS:
                        response_dict = {{
                            'dates': ['2024-01', '2024-02'],
                            'values': [100, 200]
                        }}
                        
                        CORRECT Usage:
                        pythonCopy# CORRECT - Use response_dict as is:
                        df = pd.DataFrame(response_dict)
                        
                        The ONLY valid operations on response_dict:
                        ---------------------------------------------------------
                        Reading from it
                        Converting to DataFrame
                        Accessing its columns
                        Filtering/transforming its data 
                        
                        
                        Core Requirements
                        ----------------------------------------------------------
                        
                        Data Source Rules
                        ----------------------------------------------------------
                        MUST use response_dict as the ONLY data source
                        response_dict contains query results as a list of dictionaries
                        NEVER create or assign sample/dummy data
                        NEVER hardcode values
                        NEVER modify response_dict structure
                        
                        
                        Data Processing Requirements
                        ------------------------------------------------------------
                        Convert response_dict to DataFrame: df = pd.DataFrame(response_dict)
                        Ensure all arguments have equal length
                        Use only columns available in response_dict
                        Validate data types and handle nulls if needed
                        # DateTime Handling
                        - Convert datetime strings to pandas datetime objects with proper timezone handling:
                        - For UTC timestamps: df['date_column'] = pd.to_datetime(df['date_column'], utc=True)
                        - For local time display: df['date_column'] = df['date_column'].dt.tz_localize(None)
                        - Validate timezone information in datetime columns before processing
                        
                        
                        Supported Visualization Types
                        ------------------------------------------------------------
                        Bar charts
                        Stacked bar charts
                        Line charts 
                        Pie charts (Use it only when user specifically ask for it)
                        Data cards/Big numbers (When user ask for average, total etc)
                        
                        
                        Visual Style Requirements
                        -------------------------------------------------------------
                        Use plotly_white as background layout
                        MUST include fig.show() at the end of code
                        Only generate visualization if plotly_code_generation_status is 'Yes'
                        Charts must be business-relevant and actionable
                        Use appropriate axis labels and titles
                        Include legends where applicable
                        
        
                        Color Specifications
                        -----------------------------------------------------------
                        Line Plots
                        -----------------------------------------------------
                        #ef476f, #00193b, #fb8500,#1b7b3d, #90be6d,#001e45,#eb5133, #22b455,#002657, #002d67, #00377e, #280659,#341671,#1d3557, #457b9d, #003554, #03273c, #009944
                        
                        Bar Plots
                        ------------------------------------------------------------
                        '#636EFA', '#EF553B', '#00CC96', '#AB63FA', '#FFA15A', #145da0, #0c2d48, #2e8bc0, #fd7f20, #fc2e20, #fdb750, #175873, #2b7c85, #87aca3,#0c1446
                        
                        Multiple Traces
                        --------------------------------------------------------------
                        Color rotation: ['#1f77b4', '#2ca02c', '#9467bd', '#d62728']
                        Cool professional : ['#2b6cb0', '#319795', '#553c9a', '#1a365d', '#234e52']
                        Cool contrast : ['#4299e1', '#38b2ac', '#6b46c1', '#2d3748', '#805ad5']
                        Cool pastel: ['#90cdf4', '#81e6d9', '#9f7aea', '#a0aec0', '#b2f5ea']
                        
                        NEVER reuse colors within the same analysis
                        
                        Required Code Structure
                        -------------------------------------------------------------
                        
                        import plotly.express as px
                        import pandas as pd
                        
                        # 1. Data Processing
                        df = pd.DataFrame(response_dict)
                        
                        # Simple datetime handling - convert to string format
                        datetime_columns = [col for col in df.columns if 'date' in col.lower() or 'time' in col.lower()]
                        for col in datetime_columns:
                            try:
                                # Convert to string format YYYY-MM-DD
                                df[col] = pd.to_datetime(df[col]).dt.strftime('%Y-%m-%d')
                            except:
                                # If conversion fails, keep original data
                                continue
                        
                        # 2. Create Visualization
                        fig = px.line(df, 
                                      x='actual_column_name',
                                      y='actual_column_name',
                                      title='Meaningful Title')
                        
                        # 3. Update Layout
                        fig.update_layout(
                            template='plotly_white',
                            xaxis_title="X Axis Label",
                            yaxis_title="Y Axis Label"
                        )
                        
                        # 4. Display (REQUIRED)
                        fig.show()
                        
                        Common Mistakes to Avoid
                        -----------------------------------------
                        Data Source Errors:
                        pythonCopy# WRONG - Creating sample data
                        response_dict = []
                        
                        # WRONG - Modifying response_dict
                        response_dict.append({{'date': '2024-01'}})
                        
                        # WRONG - Reassigning response_dict
                        response_dict = pd.DataFrame()
                        
                        # WRONG - Using dummy data
                        test_data = {{'col1': [1,2,3]}}
                        
                        Code Structure Errors:
                        
                        Missing fig.show()
                        Not using plotly_white template
                        Hardcoding colors not from specification
                        Reusing colors across visualizations
                        Missing axis labels or titles
                        
                        DateTime Handling:
                        # CORRECT - Simple string conversion
                        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
                        
                        # For time-specific data
                        df['datetime'] = pd.to_datetime(df['datetime']).dt.strftime('%Y-%m-%d %H:%M:%S')
                        
                        
                        Data Processing Errors:
                        ----------------------------------------------------------
                        Creating dummy DataFrames
                        Using columns that might not exist
                        Not handling potential null values
                        Not validating data types
                
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
                        - Very Very Important: ⚠️⚠️ , If user ask for a specific person by you can search whether that name is their in sales consultant, customer's data. Never say he/she is a customer or sales consultant without knowing.
                        - Only provide information about Sysco Labs
                        - For non-Sysco Labs questions: "I focus exclusively on Sysco Labs information. Would you like to know about [relevant Sysco Labs topic]? 🎯"
                        - Avoid political, controversial, or non-business topics
                        - If unable to answer: "While I specialize in Sysco Labs information, I don't have enough details to answer this specific question. However, I'd be happy to tell you about [related Sysco Labs topic] 💡"
                        - Professional Emojis is must
                        - If you do long explanations provide answers point wise
                        - Very Very Important: ⚠️⚠️If the user question is not related to visualizations, stat, numeric,or comparison data, do not ever provide visualizations.
                        
                        When asked about specific individuals:
                        --------------------------------------------------------------------------------------------------
                        Only confirm someone's status as a customer or sales consultant if you have actually searched and found their name in the relevant database
                        If you cannot verify the person in the database, respond with: "I'll need to check our records to confirm their status. Could you please provide more details?"
                        Never make assumptions about whether someone is a customer or sales consultant without database verification
                        
                        # Stay Focused and Contextual
                        - Provide responses that are strictly relevant to the questions asked about Sysco Labs
                        - Do not include any information beyond the scope of the specific query or subject matter being discussed
                        - Avoid providing out-of-context or irrelevant information in any responses
                        - Maintain focus on the exact question asked and its direct implications for Sysco Labs
                        - If question is unclear, ask for clarification rather than making assumptions
                        - Never add extraneous information not directly related to the query
                        
                        # Strictly follow this setting prompts as well
                        {settings_prompt}  
"""


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
    formatted_prompt = QUERY_CHAT_PROMPT.format(
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

        # Prepare settings prompt for the user
        settings_prompt = f"""
               Each setting text generation pattern explanation:
                   **Politeness Level**
                    Friendly - Warm, welcoming tone with personal touches and encouraging language
                    - Key terms: "Great to hear from you!", "I'd love to help", "That's wonderful"
                    - Emotionally engaging phrases: "I understand how you feel", "I'm excited to assist"
                    - Personal pronouns: frequent use of "you" and "we"
                    - Encouraging language: "You're doing great", "That's a fantastic approach"
                    
                    Neutral - Balanced, straightforward communication without strong emotional elements
                    - Key terms: "Here is", "This shows", "The following"
                    - Matter-of-fact phrases: "Based on the information", "According to"
                    - Objective language: "The data indicates", "Results show"
                    - Clear statements: "This works by", "The process involves"
                    
                    Professional - Courteous, respectful tone with proper etiquette and business-appropriate language
                    - Key terms: "I appreciate your inquiry", "Thank you for your consideration"
                    - Business phrases: "Per your request", "As discussed", "I am pleased to"
                    - Formal acknowledgments: "Kind regards \n SyscoPulse Team", "Best regards \n SyscoPulse Team" (Mention SyscoPulse Team)
                    - Respectful language: "Would you please", "May I suggest"
                    
                  **Tone**
                    Formal - Structured, sophisticated language with proper grammar and technical terminology
                    - Key terms: "Furthermore", "Nevertheless", "Subsequently"
                    - Complete sentences: No contractions, full proper nouns
                    - Technical terms: Industry-specific vocabulary, precise terminology
                    - Complex structures: "In accordance with", "With regard to"
                    
                    Semi-formal - Balanced mix of professional and conversational elements, moderate use of contractions
                    - Key terms: "I'd suggest", "Let's consider", "We'll examine"
                    - Mixed contractions: Selective use of don't, can't, we'll
                    - Balanced phrases: "This means that", "In other words"
                    - Accessible terminology: Mix of technical and common terms
                    
                    Informal - Casual, conversational style with common expressions and simplified language
                    - Key terms: "Yeah", "Sure thing", "No worries"
                    - Contractions: heavy use of don't, can't, won't, it's
                    - Casual phrases: "Basically", "Kind of", "Pretty much"
                    - Everyday language: Common expressions, simple terms

                  **responseLength**
                    Brief - Concise response focusing on essential information (50-100 words)
                    Medium - Balanced explanation with supporting details (100-250 words)
                    Detailed - Comprehensive coverage with examples and thorough explanations (250+ words)
                  
               Response Settings:
               - Language: {question.settings.language}
               - Politeness Level: {question.settings.politeness_level}
               - Formality: {question.settings.formality}
               - Response Length: {question.settings.response_length}
               - Current time: {question.time}
               - Current date: {question.date}

               Please adjust your response according to these settings:
               1. Use the specified {question.settings.language} language
               2. Maintain {question.settings.politeness_level} politeness level
               3. Keep {question.settings.formality} tone
               5. Provide {question.settings.response_length} response length
               6. Use emojis based on above selected settings
               """

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
def generate_plot_analysis(data: str, question: Question):
    try:
        # Prepare prompt for LLM
        analysis_prompt = f"""
                Users question: {question.question}
                If this data contains not only null values return 'yes'.
                If the data only contains null values say you cannot generate an answer with data table at the moment in a creative way with emojis bit longer explanation.
                Do not say null values are there in the data. just say you can't generate an answer at this moment by using user's question. (don not use the term with provided data, say with existing data that I can access)
                Say it professionally and use emojis.
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
                    result["table_accept_status"] = generate_plot_analysis(result["data"][:300], question) # Generate table analysis

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

            chat_histories[session_id]["answers"].append([result["text_explanation"], result["query"], result["plotly_code"], result["chart_analysis"]]) # Append the text explanation to chat history

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
