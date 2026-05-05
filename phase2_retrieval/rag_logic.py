import os
import google.generativeai as genai
from database import perform_semantic_retrieval

# Load API key
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Mock Project Config (Since UI is not yet connected to DB)
TENANT_CONFIGS = {
    "education": {
        "name": "Academic Advisor",
        "instructions": "You are a helpful Academic Advisor. Use the provided context to answer student queries about courses and policies.",
        "guardrails": "Do not provide personal opinions. Only answer based on the knowledge base.",
        "buttons": ["Course Details", "Contact Admissions"]
    },
    "event": {
        "name": "Event Coordinator",
        "instructions": "You are an Event Coordinator. Help users with event schedules, locations, and registration details.",
        "guardrails": "Only provide information about existing events in the database.",
        "buttons": ["Register Now", "View Schedule"]
    },
    "hospital": {
        "name": "Health Assistant",
        "instructions": "You are a Medical Information Assistant. Provide general information about hospital services and departments.",
        "guardrails": "NEVER provide medical diagnosis. Always recommend seeing a doctor for symptoms.",
        "buttons": ["Book Appointment", "Emergency Contacts"]
    },
    "real_estate": {
        "name": "Property Agent",
        "instructions": "You are a Property Agent. Help users find information about listings, amenities, and pricing.",
        "guardrails": "Do not guarantee prices. Mention that prices are subject to change.",
        "buttons": ["Schedule Visit", "View Floor Plan"]
    },
    "tourism": {
        "name": "Travel Guide",
        "instructions": "You are a local Travel Guide. Help users discover attractions and travel tips based on the context.",
        "guardrails": "Focus only on the destination mentioned in the knowledge base.",
        "buttons": ["Book Tour", "Local Weather"]
    }
}

def get_tenant_context(kb_id):
    """
    Fetch project metadata (Instructions, Guardrails) based on knowledge_base_id.
    """
    return TENANT_CONFIGS.get(kb_id, {
        "name": "AI Assistant",
        "instructions": "You are a helpful AI assistant.",
        "guardrails": "Be polite and accurate.",
        "buttons": ["Learn More"]
    })

def generate_rag_response(query, chunks, kb_id):
    """
    Orchestrates the final call to Gemini using retrieved context and tenant rules.
    """
    config = get_tenant_context(kb_id)
    
    # Construct context string
    context_text = "\n\n".join([f"Source: {c['topic_name']}\nContent: {c['text']}" for c in chunks])
    
    prompt = f"""
    SYSTEM INSTRUCTIONS:
    {config['instructions']}
    
    GUARDRAILS:
    {config['guardrails']}
    
    CRITICAL LANGUAGE INSTRUCTION:
    1. Detect the language of the USER QUERY.
    2. You MUST generate your entire response in that EXACT SAME language. For example, if the query is in Hindi, you must respond in Hindi.
    
    CONTEXT FROM KNOWLEDGE BASE:
    {context_text}
    
    USER QUERY:
    {query}
    
    Generate a concise, helpful response. If the information is not in the context, say you don't know.
    """
    
    model = genai.GenerativeModel('gemini-3-flash-preview')
    response = model.generate_content(prompt)
    
    return response.text, config
