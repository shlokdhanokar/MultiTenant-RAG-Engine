import os
import csv
from datetime import datetime, timezone

# Pricing per 1M tokens as of May 2026
PRICING = {
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 5.0, "output": 15.0},
}

LOG_FILE_PATH = os.path.join(os.path.dirname(__file__), "openai_token_log.csv")

def log_openai_expenditure(operation, model, prompt_tokens, completion_tokens=0, total_tokens=0):
    """
    Automatically logs OpenAI token usage and cost to a local CSV file.
    Calculates cost based on model input/output rates.
    """
    now = datetime.now() # Local time
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    
    # Cost calculation
    pricing_info = PRICING.get(model, {"input": 0.0, "output": 0.0})
    input_cost = (prompt_tokens / 1_000_000) * pricing_info.get("input", 0.0)
    output_cost = (completion_tokens / 1_000_000) * pricing_info.get("output", 0.0)
    cost = input_cost + output_cost
    
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens
        
    # Headers: Date, Time, Operation, Model, Input Tokens, Output Tokens, Total Tokens, Cost (USD)
    file_exists = os.path.exists(LOG_FILE_PATH)
    
    try:
        with open(LOG_FILE_PATH, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "Date", 
                    "Time", 
                    "Operation", 
                    "Model", 
                    "Input Tokens", 
                    "Output Tokens", 
                    "Total Tokens", 
                    "Cost (USD)"
                ])
            
            writer.writerow([
                date_str,
                time_str,
                operation,
                model,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                f"${cost:.6f}"
            ])
            
        print(f"  [TOKEN LOGGER] Automatically logged: {operation} | {model} | Cost: ${cost:.6f}")
    except Exception as e:
        print(f"  [TOKEN LOGGER] Failed to write log: {e}")
        
    return cost


def backfill_weekly_logs():
    """
    Scans the MongoDB chathistories collection for any RAG responses 
    generated during the current week, calculates cost, and writes them to the CSV.
    """
    from database import db
    from datetime import datetime, timedelta, timezone
    
    # Calculate start of this week (Monday)
    now = datetime.now(timezone.utc)
    # now.weekday() returns 0 for Monday, 6 for Sunday
    start_of_week = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    
    print(f"  [TOKEN LOGGER] Scanning MongoDB for RAG history since {start_of_week.strftime('%Y-%m-%d %H:%M:%S')} UTC...")
    
    # Find all sessions with messages
    sessions = db["chathistories"].find({"messages": {"$exists": True, "$not": {"$size": 0}}})
    
    count = 0
    records = []
    
    for sess in sessions:
        for msg in sess.get("messages", []):
            # Check if message is from AI, has token info, and is within this week
            if msg.get("sender") == "ai" and msg.get("totalTokens", 0) > 0:
                created_at = msg.get("createdAt")
                if created_at:
                    # Make offset-aware if needed
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    
                    if created_at >= start_of_week:
                        prompt_tokens = msg.get("promptTokens", 0)
                        completion_tokens = msg.get("completionTokens", 0)
                        total_tokens = msg.get("totalTokens", 0)
                        
                        # Date and time in local time
                        local_dt = created_at.astimezone()
                        date_str = local_dt.strftime("%Y-%m-%d")
                        time_str = local_dt.strftime("%H:%M:%S")
                        
                        records.append({
                            "Date": date_str,
                            "Time": time_str,
                            "Operation": "RAG Response (Restored)",
                            "Model": "gpt-4o-mini",
                            "Input Tokens": prompt_tokens,
                            "Output Tokens": completion_tokens,
                            "Total Tokens": total_tokens,
                        })
                        count += 1
                        
    if not records:
        print("  [TOKEN LOGGER] No historical logs found for this week in MongoDB.")
        return 0
        
    # Sort records chronologically
    records.sort(key=lambda x: (x["Date"], x["Time"]))
    
    # Read existing entries to prevent duplication
    existing_keys = set()
    if os.path.exists(LOG_FILE_PATH):
        try:
            with open(LOG_FILE_PATH, mode='r', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader, None) # skip header
                for row in reader:
                    if len(row) >= 7:
                        # Key by Date, Time, Operation, Model
                        existing_keys.add((row[0], row[1], row[2], row[3]))
        except Exception:
            pass

    # Write to CSV
    file_exists = os.path.exists(LOG_FILE_PATH)
    written = 0
    
    try:
        with open(LOG_FILE_PATH, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "Date", 
                    "Time", 
                    "Operation", 
                    "Model", 
                    "Input Tokens", 
                    "Output Tokens", 
                    "Total Tokens", 
                    "Cost (USD)"
                ])
                file_exists = True
                
            for rec in records:
                key = (rec["Date"], rec["Time"], rec["Operation"], rec["Model"])
                if key in existing_keys:
                    continue # Skip duplicates
                
                # Calculate cost
                pricing_info = PRICING.get(rec["Model"], {"input": 0.0, "output": 0.0})
                input_cost = (rec["Input Tokens"] / 1_000_000) * pricing_info["input"]
                output_cost = (rec["Output Tokens"] / 1_000_000) * pricing_info["output"]
                cost = input_cost + output_cost
                
                writer.writerow([
                    rec["Date"],
                    rec["Time"],
                    rec["Operation"],
                    rec["Model"],
                    rec["Input Tokens"],
                    rec["Output Tokens"],
                    rec["Total Tokens"],
                    f"${cost:.6f}"
                ])
                written += 1
                
        print(f"  [TOKEN LOGGER] Successfully restored {written} historical records from this week.")
    except Exception as e:
        print(f"  [TOKEN LOGGER] Failed to backfill logs: {e}")
        
    return written


if __name__ == "__main__":
    backfill_weekly_logs()
