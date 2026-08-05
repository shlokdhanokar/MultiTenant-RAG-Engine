        # ===== HELPER: LLM Semantic Selection =====
        def resolve_selection_with_llm(user_input, options, type_name="option"):
            import json
            from server import openai_client
            from token_logger import log_openai_expenditure
            
            options_str = json.dumps([{"id": opt.get("index"), "label": opt.get("label", opt.get("name", str(opt))), "details": opt} for opt in options], default=str)
            prompt = f"You are an AI assistant helping to map a user's free-text utterance to one of the available {type_name} options.\n\nAvailable Options:\n{options_str}\n\nUser Utterance: \"{user_input}\"\n\nAnalyze the utterance. If it semantically matches one of the options, return a JSON object with a single key 'selected_id' containing the integer ID (the 'id' field). If the user is clearly changing the subject, asking a new question, or wanting to cancel (e.g. searching for a product, asking a general question), return {{\"selected_id\": \"BREAKOUT\"}}. Otherwise, if it doesn't match any option, return {{\"selected_id\": null}}."
            
            try:
                resp = openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    response_format={"type": "json_object"}
                )
                usage = resp.usage
                log_openai_expenditure("Semantic State Resolver", "gpt-4o-mini", usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
                content = resp.choices[0].message.content
                parsed = json.loads(content)
                selected_id = parsed.get("selected_id")
                if str(selected_id) == "BREAKOUT":
                    return "BREAKOUT"
                if selected_id:
                    for opt in options:
                        if opt.get("index") == selected_id:
                            return opt
            except Exception as e:
                print(f"Failed to resolve selection with LLM: {e}")
            return None

        is_breakout = False

        if current_step == "awaiting_email":
            # The user's message IS their email address
            from phase4_integrations.marketplace_auth import initiate_otp_login
            result = initiate_otp_login(query.strip(), session_id)
            ai_text = result["message"]
            tenant_config = project_config

            ai_text = translate_action_response(ai_text, query)

            save_chat_message(session_id, "user", query)
            save_chat_message(session_id, "ai", ai_text)
            base_url = request.host_url.rstrip('/')
            return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None

        elif current_step == "awaiting_otp":
            # The user's message IS the OTP
            from phase4_integrations.marketplace_auth import verify_otp_login
            result = verify_otp_login(query.strip(), session_id, session_obj)
            ai_text = result["message"]
            tenant_config = project_config

            ai_text = translate_action_response(ai_text, query)

            save_chat_message(session_id, "user", query)
            save_chat_message(session_id, "ai", ai_text)
            base_url = request.host_url.rstrip('/')
            return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None

        # ===== CHECKOUT STATE: Address Selection =====
        elif current_step == "awaiting_address_selection":
            from database import update_marketplace_state
            from phase4_integrations.marketplace_auth import handle_marketplace_action
            addresses = marketplace_state.get("checkout_addresses", [])
            user_input = query.strip()
            
            selected_addr = None
            try:
                idx = int(user_input)
                for a in addresses:
                    if a["index"] == idx:
                        selected_addr = a
                        break
            except ValueError:
                for a in addresses:
                    if a.get("label", "").lower() in user_input.lower() or user_input.lower() in a.get("label", "").lower():
                        selected_addr = a
                        break
            
            if not selected_addr:
                selected_addr = resolve_selection_with_llm(user_input, addresses, "address")
                
            if selected_addr == "BREAKOUT":
                from database import update_marketplace_state
                update_marketplace_state(session_id, {"current_step": "idle"})
                is_breakout = True
            elif not selected_addr:
                ai_text = f"I couldn't match that to any address. Please reply with a number (1-{len(addresses)})."
                tenant_config = project_config
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None
            
            if not is_breakout:
                import datetime as _dt
                today = _dt.date.today()
                date_options = [
                    {"date": today.isoformat(), "label": "Today", "index": 1},
                    {"date": (today + _dt.timedelta(days=1)).isoformat(), "label": "Tomorrow", "index": 2},
                    {"date": (today + _dt.timedelta(days=2)).isoformat(), "label": "Day After Tomorrow", "index": 3},
                    {"date": "__custom__", "label": "Choose a Custom Date", "index": 4},
                ]
                
                update_marketplace_state(session_id, {
                    "current_step": "awaiting_date_selection",
                    "selectedAddressId": selected_addr["id"],
                    "checkout_dates": date_options,
                })
                
                ai_text = "\U0001F4C5 *Select a Delivery Date:*\n\n"
                for opt in date_options:
                    if opt["date"] == "__custom__":
                        ai_text += f"*{opt['index']}. {opt['label']}*\n"
                    else:
                        ai_text += f"*{opt['index']}. {opt['label']}* ({opt['date']})\n"
                ai_text += "\nReply with the *number* of your preferred delivery date, or tell me the date in words (e.g. \"Thursday\", \"June 25th\")."
                
                buttons = [
                    {"id": "1", "title": "Today"},
                    {"id": "2", "title": "Tomorrow"},
                    {"id": "3", "title": "Day After Tomorrow"},
                ]
                wp_payload = {
                    "type": "session_quick_reply_with_text",
                    "media": {
                        "header": {"text": ""},
                        "body": ai_text,
                        "footer_text": "",
                        "button": buttons,
                    },
                }
                
                tenant_config = project_config
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload

        # ===== CHECKOUT STATE: Date Selection =====
        elif current_step == "awaiting_date_selection":
            from database import update_marketplace_state
            from phase4_integrations.marketplace_auth import handle_marketplace_action
            date_options = marketplace_state.get("checkout_dates", [])
            user_input = query.strip()
            
            selected_date = None
            try:
                idx = int(user_input)
                for d in date_options:
                    if d["index"] == idx:
                        selected_date = d
                        break
            except ValueError:
                for d in date_options:
                    if d.get("label", "").lower() in user_input.lower() or user_input.lower() in d.get("label", "").lower():
                        selected_date = d
                        break
            
            if not selected_date:
                selected_date = resolve_selection_with_llm(user_input, date_options, "delivery date")
                
            if selected_date == "BREAKOUT":
                from database import update_marketplace_state
                update_marketplace_state(session_id, {"current_step": "idle"})
                is_breakout = True
            elif not selected_date:
                ai_text = f"I couldn't match that to any date. Please reply with a number (1-{len(date_options)}), or say the date in words."
                tenant_config = project_config
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None
            
            if not is_breakout:
                if selected_date.get("date") == "__custom__":
                    update_marketplace_state(session_id, {
                        "current_step": "awaiting_custom_date",
                    })
                    ai_text = "\U0001F4C5 Please tell me the date you'd like delivery on.\n\n"
                    ai_text += "You can say things like:\n"
                    ai_text += "• *June 25*\n• *Thursday*\n• *25th of this month*\n• *next Monday*\n\n"
                    ai_text += "Or type the date as *YYYY-MM-DD* (e.g. 2026-06-25)."
                    tenant_config = project_config
                    save_chat_message(session_id, "user", query)
                    save_chat_message(session_id, "ai", ai_text)
                    base_url = request.host_url.rstrip('/')
                    return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None
                
                import datetime as _dt
                selected_date_str = selected_date["date"]
                is_today = (selected_date_str == _dt.date.today().isoformat())
                now = _dt.datetime.now()
                
                ALL_SLOTS = [
                    {"name": "8 AM - 10 AM",  "startTime": "08:00", "endTime": "10:00"},
                    {"name": "10 AM - 12 PM", "startTime": "10:00", "endTime": "12:00"},
                    {"name": "12 PM - 2 PM",  "startTime": "12:00", "endTime": "14:00"},
                    {"name": "2 PM - 4 PM",   "startTime": "14:00", "endTime": "16:00"},
                    {"name": "4 PM - 6 PM",   "startTime": "16:00", "endTime": "18:00"},
                    {"name": "6 PM - 8 PM",   "startTime": "18:00", "endTime": "20:00"},
                ]
                
                available_slots = []
                for s in ALL_SLOTS:
                    if is_today:
                        slot_start = _dt.datetime.strptime(s["startTime"], "%H:%M").replace(
                            year=now.year, month=now.month, day=now.day
                        )
                        if slot_start <= now:
                            continue
                    available_slots.append(s)
                
                if not available_slots:
                    ai_text = "\u23F0 Sorry, all delivery slots for today have already passed. Please choose a different date."
                    update_marketplace_state(session_id, {
                        "current_step": "awaiting_date_selection",
                    })
                    tenant_config = project_config
                    save_chat_message(session_id, "user", query)
                    save_chat_message(session_id, "ai", ai_text)
                    base_url = request.host_url.rstrip('/')
                    return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None
                
                slot_options = []
                ai_text = "\u23F0 *Available Delivery Slots:*\n\n"
                for idx, s in enumerate(available_slots, 1):
                    slot_options.append({
                        "id": f"local_slot_{idx}",
                        "name": s["name"],
                        "label": s["name"],
                        "timing": s["name"],
                        "startTime": s["startTime"],
                        "endTime": s["endTime"],
                        "index": idx,
                    })
                    ai_text += f"*{idx}. {s['name']}*\n"
                ai_text += "\nReply with the *number* of the slot you'd like, or say it in words (e.g. \"morning\", \"4 to 6\")."
                
                update_marketplace_state(session_id, {
                    "current_step": "awaiting_slot_selection",
                    "selectedDeliveryDate": selected_date_str,
                    "checkout_slots": slot_options,
                })
                
                buttons = []
                for opt in slot_options[:3]:
                    buttons.append({"id": str(opt["index"]), "title": opt["name"][:20]})
                wp_payload = {
                    "type": "session_quick_reply_with_text",
                    "media": {
                        "header": {"text": "Delivery Slots"},
                        "body": ai_text,
                        "footer_text": "",
                        "button": buttons,
                    },
                }
                
                tenant_config = project_config
                ai_text = translate_action_response(ai_text, query)
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload

        # ===== CHECKOUT STATE: Custom Date Entry =====
        elif current_step == "awaiting_custom_date":
            from database import update_marketplace_state
            import datetime as _dt
            user_input = query.strip()
            
            parsed_date = None
            try:
                parsed_date = _dt.datetime.strptime(user_input, "%Y-%m-%d").date()
            except ValueError:
                pass
            
            if not parsed_date:
                for fmt in ["%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%B %d", "%b %d", "%d %B", "%d %b"]:
                    try:
                        d = _dt.datetime.strptime(user_input, fmt).date()
                        if d.year == 1900:
                            d = d.replace(year=_dt.date.today().year)
                        parsed_date = d
                        break
                    except ValueError:
                        continue
            
            if not parsed_date:
                import json
                from server import openai_client
                try:
                    resp = openai_client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{"role": "user", "content": f"Today is {_dt.date.today().isoformat()} ({_dt.date.today().strftime('%A')}). The user said: \"{user_input}\". What date are they referring to? Return ONLY a JSON object with key 'date' in YYYY-MM-DD format. If the user is clearly changing the subject, asking a new question, or wanting to cancel (e.g. searching for a product), return {{\"date\": \"BREAKOUT\"}}. If you cannot determine a date, return {{\"date\": null}}."}],
                        temperature=0,
                        response_format={"type": "json_object"}
                    )
                    from token_logger import log_openai_expenditure
                    usage = resp.usage
                    log_openai_expenditure("Custom Date Parser", "gpt-4o-mini", usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
                    result = json.loads(resp.choices[0].message.content)
                    date_str = result.get("date")
                    if date_str == "BREAKOUT":
                        from database import update_marketplace_state
                        update_marketplace_state(session_id, {"current_step": "idle"})
                        parsed_date = "BREAKOUT"
                    elif date_str:
                        parsed_date = _dt.datetime.strptime(date_str, "%Y-%m-%d").date()
                except Exception as e:
                    print(f"  [CUSTOM DATE] LLM parse failed: {e}")
            
            if parsed_date == "BREAKOUT":
                is_breakout = True
            elif not parsed_date or parsed_date < _dt.date.today():
                ai_text = "I couldn't understand that date, or it's in the past. Please try again.\n\nExamples: *June 25*, *Thursday*, *next Monday*, or *2026-06-25*."
                tenant_config = project_config
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None
            
            if not is_breakout:
                selected_date_str = parsed_date.isoformat()
                is_today = (selected_date_str == _dt.date.today().isoformat())
                now = _dt.datetime.now()
                
                ALL_SLOTS = [
                    {"name": "8 AM - 10 AM",  "startTime": "08:00", "endTime": "10:00"},
                    {"name": "10 AM - 12 PM", "startTime": "10:00", "endTime": "12:00"},
                    {"name": "12 PM - 2 PM",  "startTime": "12:00", "endTime": "14:00"},
                    {"name": "2 PM - 4 PM",   "startTime": "14:00", "endTime": "16:00"},
                    {"name": "4 PM - 6 PM",   "startTime": "16:00", "endTime": "18:00"},
                    {"name": "6 PM - 8 PM",   "startTime": "18:00", "endTime": "20:00"},
                ]
                
                available_slots = []
                for s in ALL_SLOTS:
                    if is_today:
                        slot_start = _dt.datetime.strptime(s["startTime"], "%H:%M").replace(
                            year=now.year, month=now.month, day=now.day
                        )
                        if slot_start <= now:
                            continue
                    available_slots.append(s)
                
                if not available_slots:
                    ai_text = "\u23F0 Sorry, all delivery slots for today have already passed. Please choose a different date."
                    update_marketplace_state(session_id, {
                        "current_step": "awaiting_custom_date",
                    })
                    tenant_config = project_config
                    save_chat_message(session_id, "user", query)
                    save_chat_message(session_id, "ai", ai_text)
                    base_url = request.host_url.rstrip('/')
                    return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None
                
                slot_options = []
                ai_text = f"\U0001F4C5 Delivery on *{parsed_date.strftime('%A, %B %d')}*\n\n\u23F0 *Available Delivery Slots:*\n\n"
                for idx, s in enumerate(available_slots, 1):
                    slot_options.append({
                        "id": f"local_slot_{idx}",
                        "name": s["name"],
                        "label": s["name"],
                        "timing": s["name"],
                        "startTime": s["startTime"],
                        "endTime": s["endTime"],
                        "index": idx,
                    })
                    ai_text += f"*{idx}. {s['name']}*\n"
                ai_text += "\nReply with the *number* of the slot you'd like."
                
                update_marketplace_state(session_id, {
                    "current_step": "awaiting_slot_selection",
                    "selectedDeliveryDate": selected_date_str,
                    "checkout_slots": slot_options,
                })
                
                buttons = []
                for opt in slot_options[:3]:
                    buttons.append({"id": str(opt["index"]), "title": opt["name"][:20]})
                wp_payload = {
                    "type": "session_quick_reply_with_text",
                    "media": {
                        "header": {"text": "Delivery Slots"},
                        "body": ai_text,
                        "footer_text": "",
                        "button": buttons,
                    },
                }
                
                tenant_config = project_config
                ai_text = translate_action_response(ai_text, query)
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload

        # ===== CHECKOUT STATE: Slot Selection =====
        elif current_step == "awaiting_slot_selection":
            from database import update_marketplace_state
            from phase4_integrations.marketplace_auth import handle_marketplace_action
            slots = marketplace_state.get("checkout_slots", [])
            user_input = query.strip()
            
            selected_slot = None
            try:
                idx = int(user_input)
                for s in slots:
                    if s["index"] == idx:
                        selected_slot = s
                        break
            except ValueError:
                for s in slots:
                    if s.get("name", "").lower() in user_input.lower() or user_input.lower() in s.get("name", "").lower():
                        selected_slot = s
                        break
            
            if not selected_slot:
                selected_slot = resolve_selection_with_llm(user_input, slots, "delivery time slot")
                
            if selected_slot == "BREAKOUT":
                from database import update_marketplace_state
                update_marketplace_state(session_id, {"current_step": "idle"})
                is_breakout = True
            elif not selected_slot:
                ai_text = f"I couldn't match that to any slot. Please reply with a number (1-{len(slots)})."
                tenant_config = project_config
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, None
            
            if not is_breakout:
                selected_address_id = marketplace_state.get("selectedAddressId", "")
                update_marketplace_state(session_id, {
                    "current_step": "idle",
                    "selectedSlotId": selected_slot["id"],
                    "selectedSlotName": selected_slot.get("name", ""),
                    "selectedSlotTiming": selected_slot.get("timing", ""),
                })
                
                result = handle_marketplace_action(
                    session=session_obj,
                    session_id=session_id,
                    action_id="calculate_delivery_charge",
                    parameters={"customerDeliveryAddressId": selected_address_id}
                )
                ai_text = result["message"]
                wp_payload = result.get("whatsapp_payload")
                
                tenant_config = project_config
                ai_text = translate_action_response(ai_text, query)
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload

        # ===== CHECKOUT STATE: Coupons or Place Order =====
        elif current_step == "awaiting_checkout_decision":
            from database import update_marketplace_state
            from phase4_integrations.marketplace_auth import handle_marketplace_action
            user_input = query.strip().lower()
            
            if any(w in user_input for w in ["coupon", "discount", "code", "coupons"]):
                update_marketplace_state(session_id, {"current_step": "idle"})
                result = handle_marketplace_action(
                    session=session_obj,
                    session_id=session_id,
                    action_id="list_coupons",
                    parameters={}
                )
                ai_text = result["message"]
                wp_payload = result.get("whatsapp_payload")
                tenant_config = project_config
                ai_text = translate_action_response(ai_text, query)
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload
            elif any(w in user_input for w in ["place", "order", "yes", "proceed", "skip", "no coupon"]):
                update_marketplace_state(session_id, {"current_step": "idle"})
                result = handle_marketplace_action(
                    session=session_obj,
                    session_id=session_id,
                    action_id="create_order",
                    parameters={}
                )
                ai_text = result["message"]
                wp_payload = result.get("whatsapp_payload")
                tenant_config = project_config
                ai_text = translate_action_response(ai_text, query)
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload
            else:
                update_marketplace_state(session_id, {"current_step": "idle"})
                is_breakout = True

        # ===== CHECKOUT STATE: Coupon Selection =====
        elif current_step == "awaiting_coupon_selection":
            from database import update_marketplace_state
            from phase4_integrations.marketplace_auth import handle_marketplace_action
            user_input = query.strip()
            
            if user_input.lower() in ("skip", "no", "no coupon", "none"):
                update_marketplace_state(session_id, {"current_step": "idle"})
                result = handle_marketplace_action(
                    session=session_obj,
                    session_id=session_id,
                    action_id="create_order",
                    parameters={}
                )
                ai_text = result["message"]
                wp_payload = result.get("whatsapp_payload")
                tenant_config = project_config
                ai_text = translate_action_response(ai_text, query)
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload
            elif len(user_input.split()) > 2:
                # Likely a breakout statement rather than a coupon code
                update_marketplace_state(session_id, {"current_step": "idle"})
                is_breakout = True
            else:
                update_marketplace_state(session_id, {"current_step": "idle"})
                result = handle_marketplace_action(
                    session=session_obj,
                    session_id=session_id,
                    action_id="verify_coupon",
                    parameters={"couponCode": user_input}
                )
                ai_text = result["message"]
                wp_payload = result.get("whatsapp_payload")
                tenant_config = project_config
                ai_text = translate_action_response(ai_text, query)
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload

        # ===== CHECKOUT STATE: Order Confirmation =====
        elif current_step == "awaiting_order_confirmation":
            from database import update_marketplace_state
            from phase4_integrations.marketplace_auth import handle_marketplace_action
            user_input = query.strip().lower()
            
            if user_input in ("yes", "y", "confirm", "ha", "haan", "ok", "place order", "place", "proceed"):
                update_marketplace_state(session_id, {"current_step": "idle"})
                result = handle_marketplace_action(
                    session=session_obj,
                    session_id=session_id,
                    action_id="create_order",
                    parameters={}
                )
                ai_text = result["message"]
                wp_payload = result.get("whatsapp_payload")
            elif user_input in ("no", "cancel", "abort", "nahi"):
                update_marketplace_state(session_id, {"current_step": "idle"})
                ai_text = "Order cancelled. No worries! Your items are still in the cart if you change your mind. 🛒"
                wp_payload = None
            else:
                update_marketplace_state(session_id, {"current_step": "idle"})
                is_breakout = True
            
            if not is_breakout:
                tenant_config = project_config
                ai_text = translate_action_response(ai_text, query)
                save_chat_message(session_id, "user", query)
                save_chat_message(session_id, "ai", ai_text)
                base_url = request.host_url.rstrip('/')
                return ai_text, [], tenant_config, base_url, session_id, is_expired, project_id, wp_payload
