# Session and User ID Linkage Security Implementation

## Overview
This implementation prevents session and user ID hijacking by validating that the `session_id` and `user_id` are uniquely linked in both marketplace and chat integrations.

## Changes Made

### 1. Database Layer - `database.py`

**Modified Function:** `get_or_create_session(session_id, user_id, admin_id, project_id)`

**Key Changes:**
- Added validation to check if a session already exists
- If session exists, validates that the stored `userId` matches the requested `user_id`
- Raises `ValueError("the sessionid or userid is invalid")` on mismatch
- This prevents a different user from hijacking an existing session

**Security Flow:**
```
Session Lookup
    ↓
If Session Exists:
    ├─→ Check: session.userId == requested user_id
    ├─→ If MATCH: Return session (allow access)
    └─→ If MISMATCH: Raise ValueError (reject access)
    
If No Session:
    └─→ Create new session with user_id
```

### 2. API Layer - `server.py`

**Modified Endpoints:**
- `/chat/v2` - Standard reply array output
- `/chat/v3` - Agentic session message output

**Key Changes:**
- Added specific error handling for `ValueError` with message containing "the sessionid or userid is invalid"
- Returns `400 Bad Request` with JSON response instead of default HTML
- Maintains 500 Internal Server Error for unexpected exceptions

**Error Response Format:**
```json
{
  "error": "the sessionid or userid is invalid"
}
```

**Request/Response Flow:**
```
POST /chat/v2
{
  "query": "...",
  "user_id": "user_b",
  "session_id": "sess_from_user_a"  ← Different from session owner
}
    ↓
get_or_create_session() validates linkage
    ↓
ValueError raised: "the sessionid or userid is invalid"
    ↓
Caught by endpoint error handler
    ↓
Response: 400 {error: "the sessionid or userid is invalid"}
```

## Security Scenarios Prevented

### Scenario 1: Direct Session Hijacking
- **Attacker:** User B obtains session_id from User A
- **Attempt:** Sends message with `session_id=user_a_session` and `user_id=user_b`
- **Result:** ✗ BLOCKED - 400 error returned

### Scenario 2: Session Swapping
- **Attacker:** User A creates session_1, User B creates session_2
- **Attempt:** User B sends message with `session_id=user_a_session_1` and `user_id=user_b`
- **Result:** ✗ BLOCKED - 400 error returned

### Scenario 3: Token Injection
- **Attacker:** Modifies JWT or cookie to change user_id while keeping session_id
- **Attempt:** API receives mismatched session_id and user_id
- **Result:** ✗ BLOCKED - 400 error returned

## Allowed Scenarios

### Scenario 1: Same User, Same Session
- User A sends message with `session_id=user_a_session` and `user_id=user_a`
- **Result:** ✓ ALLOWED - Session continues

### Scenario 2: Same User, Different Sessions
- User A creates multiple sessions with different session_ids
- Each session is linked to `user_id=user_a`
- **Result:** ✓ ALLOWED - Each session works independently

### Scenario 3: New Session Creation
- User A first message doesn't include session_id
- System creates new session linked to `user_id=user_a`
- **Result:** ✓ ALLOWED - New session created

## Testing

### Automated Test Script
**Location:** `scratch/test_session_userid_security.py`

**Test Coverage:**
1. **Test 1:** Session Hijacking Prevention
   - User A creates session
   - User B attempts to use same session → BLOCKED

2. **Test 2:** Multiple Sessions for Same User
   - User A creates two different sessions
   - Both should work independently → ALLOWED

3. **Test 3:** Same User Session Reuse
   - User A creates session and sends multiple messages
   - All messages should succeed → ALLOWED

**Running Tests:**
```bash
# Setup test credentials in the script first
python scratch/test_session_userid_security.py
```

## Implementation Details

### Database Schema
Session documents in MongoDB now enforce this relationship:
```json
{
  "sessionId": "sess_abc123",
  "userId": "user_123",      ← Immutable once set
  "adminId": "admin_456",
  "projectId": "proj_789",
  "messages": [...],
  "createdAt": "2025-06-02T...",
  "updatedAt": "2025-06-02T..."
}
```

### Validation Points
1. **Primary:** Database layer (`get_or_create_session`)
   - Ensures data integrity at source
   - Catches all attempts to mismatch session/user

2. **Secondary:** API layer (chat endpoints)
   - Returns appropriate HTTP status (400 vs 500)
   - Provides JSON response for client parsing
   - Maintains security without exposing system details

## Edge Cases Handled

✓ `session_id` = None (new session) - Auto-generates new session  
✓ `user_id` = None (anonymous user) - Allows if session is also for None  
✓ Session exists, `user_id` changes - BLOCKED with 400 error  
✓ Non-existent session - Creates new session  
✓ Concurrent requests - MongoDB atomicity ensures consistency  

## Deployment Checklist

- [ ] Deploy `database.py` changes
- [ ] Deploy `server.py` changes
- [ ] Verify endpoint returns 400 JSON (not 500 HTML)
- [ ] Run `test_session_userid_security.py` against staging
- [ ] Monitor error logs for false positives
- [ ] Update client libraries if they expect 500 on mismatch
- [ ] Add monitoring for "the sessionid or userid is invalid" errors

## Backward Compatibility

✓ Existing valid sessions (matching session/user pairs) continue to work  
✓ New session creation behavior unchanged  
✓ JSON response format compatible with existing clients  
✓ Marketplace integration unchanged - validation transparent  

## Performance Impact

- **Database:** One additional field comparison (negligible)
- **API Response:** No additional database calls
- **Error Handling:** Fast-path for error case (no unnecessary processing)
