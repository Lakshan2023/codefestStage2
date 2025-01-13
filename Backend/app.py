import json
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Load JSON data
with open('sample.json', 'r') as file:
    data = json.load(file)

questions = [item['question'] for item in data['data']]


# Function to find the top 5 similar questions
def find_similar_questions(user_query, questions, top_n=5):
    # Combine user query with the questions
    corpus = questions + [user_query]

    # Compute TF-IDF vectors
    vectorizer = TfidfVectorizer()
    tfidf_matrix = vectorizer.fit_transform(corpus)

    # Compute cosine similarity of user query with questions
    similarities = cosine_similarity(tfidf_matrix[-1], tfidf_matrix[:-1]).flatten()

    # Get the top N similar questions
    similar_indices = similarities.argsort()[-top_n:][::-1]
    similar_questions = [(questions[i], similarities[i]) for i in similar_indices]

    return similar_questions

user_query = "Provide the sales consultant details with id SC0891?"
similar_questions = find_similar_questions(user_query, questions)

print(similar_questions)

