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

# In-memory storage for chat histories
chat_histories: Dict[str, List[str]] = {}


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
    # Define template as a constant
    TEMPLATE_CYPHER = {
        "query_generation_status": "Yes/No",
        "query": "OPTIONAL MATCH query here"
    }

    # Move the prompt template outside the function
    QUERY_CHAT_PROMPT = """
    You are intelligent neo4j graph based query generator, which can generate cypher queries.
    When user provide you the question you have to provide the query.

    This is the graph db created code, when you generate answers only refer the nodes, 
    elements and relationships mentioned in the graph db created code

    graph db code: {graph_db_structure}

    The below samples show similar chunks and questions for the users questions.
    It will not be the user asking question, but you might get help to understand 
    the query generation process.

    similar_chunks: {similar_chunks}

    You should provide only the query. Each query must start with OPTIONAL MATCH.

    Refer to the below user question to generate your answer.

    Users question: {question}

    The answer should be given in below structure:

    Template: {template_cypher}

    If it is not a question that you can generate a cypher query, 
    query_generation_status has to be 'No', else 'Yes' along with the answer in query.
    If status is 'No' query should be empty strings.
    """

    retry_count = 0
    max_retries = 4

    # Get similar questions (assuming this function exists)
    try:
        similar_chunks = find_similar_questions(user_question, questions, cypher_queries)
    except Exception as e:
        return f"Error finding similar questions: {str(e)}"

    # Format the prompt with all required values
    formatted_prompt = QUERY_CHAT_PROMPT.format(
        graph_db_structure=graph_db_structure,
        similar_chunks=similar_chunks,
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


def serialize_value(val):
    if pd.isna(val):
        return None
    elif isinstance(val, (pd.Timestamp, datetime)):
        return val.isoformat()
    elif isinstance(val, pd.Period):
        return val.strftime('%Y-%m-%d')  # Convert Period to string
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
    return val


@app.post("/start_session")
async def start_session():
    session_id = str(uuid.uuid4())
    chat_histories[session_id] = []
    return JSONResponse(content={"session_id": session_id})

# @app.post("/chat")
# async def chat(question: Question):
#     try:
#         session_id = question.session_id
#         user_question = question.question
#
#         if session_id not in chat_histories:
#             return JSONResponse(
#                 content={"error": "Invalid session ID"},
#                 status_code=400
#             )
#
#         answer = generate_query(user_question, session_id)
#
#         if answer is not None:
#             return JSONResponse(content={
#                 "session_id": session_id,
#                 "answer": answer
#             })
#
#     except Exception as e:
#         print(f"Error in chat endpoint: {str(e)}")
#         return JSONResponse(
#             content={
#                 "error": "An error occurred processing your request",
#                 "detail": str(e)
#             },
#             status_code=500
#         )


def execute_cypher_query(driver, query):
    """Execute a Cypher query and return the results"""
    try:
        with driver.session() as session:
            result = session.run(query)
            records = [dict(record) for record in result]
            return records
    except Exception as e:
        print(f"Error executing query: {str(e)}")
        return None


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

@app.post("/chat")
async def chat(question: Question):
    try:
        session_id = question.session_id
        user_question = question.question

        if session_id not in chat_histories:
            return JSONResponse(
                content={"error": "Invalid session ID"},
                status_code=400
            )

        # Get the query generation response
        query_response = generate_query(user_question, session_id)

        print(query_response)

        try:
            try:
                response_dict = json.loads(query_response)
            except json.JSONDecodeError:
                try:
                    response_dict = ast.literal_eval(query_response)
                except (ValueError, SyntaxError):
                    raise ValueError("Could not parse response as JSON or Python dict")

            if not isinstance(response_dict, dict) or not all(
                    key in response_dict for key in ["query_generation_status", "query"]):
                raise ValueError("Invalid response format")

            # Initialize the result
            result = {
                "query_generation_status": response_dict["query_generation_status"],
                "query": response_dict["query"],
                "data": None
            }

            # Execute the query if status is "Yes"
            if response_dict["query_generation_status"].lower() == "yes" and response_dict["query"]:
                query_result = execute_cypher_query(driver, response_dict["query"])
                # Ensure the entire result is JSON serializable
                result["data"] = json.loads(json.dumps(query_result, default=str))

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

