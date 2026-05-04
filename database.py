import os
from pymongo import MongoClient, TEXT
import gridfs
from dotenv import load_dotenv

# load environment variables
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

mongo_url = os.getenv("MONGODB_URI")
db_name = os.getenv("MONGODB_DB_NAME", "rag_db")

# connect to mongo atlas
client = MongoClient(mongo_url)
db = client[db_name]

# initialize gridfs for storing large pdfs
fs = gridfs.GridFS(db)


def get_db():
    return db


def get_gridfs():
    return fs


def create_fulltext_index():
    """
    Configure $text indexing by executing create_index([("text", TEXT)])
    on the chunks collection. This delegates to apply_index_weighting()
    which creates the index with proper field weights.
    """
    return apply_index_weighting()


def apply_index_weighting():
    """
    Assign specific weights to the text index: give the topic_name field
    a higher weight than the body text field to ensure that searches hitting
    the header title rank higher than passing mentions in the body.

    Weights:
        topic_name: 10 (highest priority)
        text: 1 (standard body text)
    """
    chunks_collection = db["chunks"]

    # drop any existing text index first to avoid conflicts
    existing_indexes = chunks_collection.index_information()
    for index_name, index_info in existing_indexes.items():
        # check if this is a text index
        if any(v == "text" for _, v in index_info.get("key", [])):
            chunks_collection.drop_index(index_name)
            print(f"dropped existing text index: {index_name}")

    # create a new weighted text index
    index_name = chunks_collection.create_index(
        [("text", TEXT), ("topic_name", TEXT)],
        weights={"topic_name": 2, "text": 10},
        name="weighted_text_index"
    )
    print(f"created weighted text index: {index_name}")
    return index_name


def perform_semantic_retrieval(query, knowledge_base_id, n=4):
    """
    Searches the MongoDB collection using $text search for a user query
    and returns the top "n" most relevant chunks for a specific knowledge base.
    """
    chunks_collection = db["chunks"]
    
    # Execute $text search with knowledge_base_id filter
    results = chunks_collection.find(
        {
            "$text": {"$search": query},
            "knowledge_base_id": knowledge_base_id
        },
        {
            "score": {"$meta": "textScore"}
        }
    ).sort([("score", {"$meta": "textScore"})]).limit(n)
    
    return list(results)


if __name__ == "__main__":
    # simple test to check connection and create indexes
    try:
        client.admin.command('ping')
        print("pinged your deployment. successfully connected to mongodb atlas!")

        # create the full-text search index
        create_fulltext_index()
        print("full-text index setup complete!")
    except Exception as e:
        print("failed:", e)
