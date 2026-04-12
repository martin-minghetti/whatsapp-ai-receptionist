# Architecture Decisions

Technical decisions made during development, with rationale. Written for hiring managers and developers who want to understand _why_, not just _what_.

## Claude over GPT for conversation

**Decision:** Use Anthropic Claude (Haiku) as the conversational AI.

**Why:** Claude follows system prompt instructions more reliably than GPT for structured output extraction. The bot needs to generate natural conversational responses AND emit structured JSON intents — Claude does this consistently without breaking the conversation flow. Haiku specifically because it's fast enough for real-time WhatsApp conversations (~300ms) and cheap enough for production use ($0.25/1M input tokens).

**Trade-off:** Vendor lock-in to Anthropic. Mitigated by keeping the AI module (`core/ai.py`) isolated — swapping to another provider requires changing one file.

## Intent extraction over function calling

**Decision:** Claude appends a JSON intent block to its natural language response, which the system extracts with regex. We don't use Claude's native tool_use/function calling.

**Why:** Function calling forces a choice: either the model calls a tool OR responds conversationally. In a booking flow, we need both — the user sees a natural confirmation message while the system gets structured data to create the calendar event. By having Claude embed the intent in its response, we get the best of both worlds.

**Trade-off:** Regex-based extraction is more fragile than native function calling. Mitigated by using a strict pattern and handling parse failures gracefully (the message still reaches the user, the intent is just lost).

## FastAPI over LangChain/LangGraph

**Decision:** Plain FastAPI with direct Anthropic SDK calls. No LangChain, no LangGraph, no agent frameworks.

**Why:** The bot has a clear request-response flow: message comes in → AI responds → intent is extracted → action is taken. There are no complex chains, no multi-step reasoning, no tool selection. Adding LangChain would add 15+ dependencies and abstractions for a problem that's solved by ~50 lines of direct API calls.

**Trade-off:** If the bot grows into a multi-agent system with complex routing, we'd need to add orchestration. For a single-purpose booking agent, direct API calls are simpler, faster, and easier to debug.

## Dual Redis/in-memory backend

**Decision:** Every stateful component (conversation history, message locks, pending payments, slot locks) has both a Redis implementation and an in-memory fallback.

**Why:** Redis is the right choice for production (persistence, TTL, shared state across workers). But requiring Redis for local development adds friction. The dual backend lets developers run `uvicorn core.main:app --reload` with zero setup — no Docker, no Redis, no infrastructure.

**How:** `history.py` pings Redis on startup. If available, uses `RedisHistory`. If not, falls back to `InMemoryHistory`. Same interface, transparent to the rest of the codebase.

## YAML config over database

**Decision:** Client configuration lives in `config.yaml` and `knowledge/client.txt`, not in a database.

**Why:** This bot serves one business at a time. A database adds complexity (migrations, connection pooling, ORM) for a problem that's solved by a YAML file. New clients are onboarded by editing two text files — no admin panel needed, no database setup.

**Trade-off:** Multi-tenant support would require a database. If this evolves into a SaaS platform serving multiple businesses, the config system would need to be rewritten. For now, YAML is the right level of complexity.

## Dynamic system prompt with pre-calculated dates

**Decision:** The system prompt is rebuilt on every request, injecting today's date and the next 5 available booking dates.

**Why:** LLMs are bad at date math. If you tell Claude "booking days are Monday, Wednesday, Friday" and today is Thursday, it might suggest Saturday. By pre-calculating the next available dates and injecting them into the prompt, we eliminate date hallucination entirely.

**Trade-off:** The system prompt is slightly longer (~200 tokens more). Worth it for 100% date accuracy.

## Per-phone message locking

**Decision:** Each phone number has a lock (Redis or in-memory) that prevents concurrent message processing.

**Why:** WhatsApp can deliver multiple messages from the same user in rapid succession (e.g., user sends "book" and "tomorrow" within 1 second). Without locking, both messages would be processed simultaneously, potentially creating duplicate bookings or corrupted conversation state.

**Trade-off:** Messages from the same user are processed sequentially, adding ~50ms latency for burst messages. Acceptable for a chat interface.

## Mercado Pago as optional module

**Decision:** Payment integration is a module that can be enabled/disabled via config.

**Why:** Not every business needs online payments with their booking system. Many professionals (especially in Latin America) prefer to collect payment in person. Making it optional keeps the core booking flow simple while allowing payment when needed.

## HMAC signature validation on all webhooks

**Decision:** Both the WhatsApp webhook and the Mercado Pago webhook validate incoming requests using HMAC-SHA256 signatures with `hmac.compare_digest` (constant-time comparison).

**Why:** Webhooks are public endpoints. Without signature validation, anyone could trigger fake bookings or fake payment confirmations. Constant-time comparison prevents timing attacks.
