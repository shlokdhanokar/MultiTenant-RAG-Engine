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
    Deprecated: We now use Vector Search instead of $text search.
    """
    pass

def apply_index_weighting():
    """
    Deprecated: We now use Vector Search instead of $text search.
    """
    pass


def perform_semantic_retrieval(query_embedding, knowledge_base_id, n=4):
    """
    Searches the MongoDB collection using $vectorSearch for a user query embedding
    and returns the top "n" most relevant chunks for a specific knowledge base.
    Requires an Atlas Vector Search Index on the 'chunks' collection.
    """
    chunks_collection = db["chunks"]
    
    pipeline = [
        {
            "$vectorSearch": {
                "index": "vector_index", # Assumed name for the text vector index
                "path": "embedding",
                "queryVector": query_embedding,
                "numCandidates": n * 10,
                "limit": n,
                "filter": {
                    "knowledge_base_id": knowledge_base_id
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "embedding": 0, # Exclude embedding to save bandwidth
                "score": { "$meta": "vectorSearchScore" }
            }
        }
    ]
    
    try:
        results = list(chunks_collection.aggregate(pipeline))
        return results
    except Exception as e:
        print(f"Text vector search failed (make sure the Atlas Vector Search Index is created): {e}")
        return []


def store_image_caption_and_vector(gridfs_id, caption, embedding, kb_id, source):
    """
    Store the generated image caption and its vector embedding in the MongoDB collection.
    """
    collection = db["image_captions"]
    doc = {
        "gridfs_id": gridfs_id,
        "caption": caption,
        "embedding": embedding,
        "knowledge_base_id": kb_id,
        "source_file": source
    }
    collection.insert_one(doc)


def perform_image_vector_search(query_embedding, knowledge_base_id, limit=2):
    """
    Perform a vector search on the image_captions collection using the query embedding.
    Requires an Atlas Vector Search Index to be created on the 'embedding' field.
    """
    collection = db["image_captions"]
    
    pipeline = [
        {
            "$vectorSearch": {
                "index": "vector_index", # Note: the user must name their index "vector_index" or default
                "path": "embedding",
                "queryVector": query_embedding,
                "numCandidates": limit * 10,
                "limit": limit,
                "filter": {
                    "knowledge_base_id": knowledge_base_id
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "gridfs_id": 1,
                "caption": 1,
                "score": { "$meta": "vectorSearchScore" }
            }
        }
    ]
    
    try:
        results = list(collection.aggregate(pipeline))
        return results
    except Exception as e:
        print(f"Vector search failed (make sure the Atlas Vector Search Index is created): {e}")
        return []


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
