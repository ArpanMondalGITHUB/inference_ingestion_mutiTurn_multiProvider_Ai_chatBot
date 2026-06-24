# Build a Multi-Turn Context Chatbot with Gemini API and a Simple Web UI

Last checked against the official Gemini API documentation on **2026-05-25**.

This tutorial teaches you how to build a simple but real chatbot that:

- supports multi-turn conversations
- keeps a short conversational context
- exposes a simple web UI
- hides your API key on the server
- can be extended with streaming, persistence, summarization, rate limiting, and deployment

We will use **Node.js**, **Express**, plain **HTML/CSS/JavaScript**, and the Google **Gemini API** through the official `@google/genai` JavaScript SDK.

The main implementation uses `gemini-3.5-flash`, because the current Gemini docs list Gemini 3.5 Flash as a stable current text model. If that model is not available in your account or region, check the Gemini models page and replace it with another available text model such as `gemini-2.5-flash`.

Official references:

- Gemini API quickstart: https://ai.google.dev/gemini-api/docs/quickstart
- Text generation and multi-turn chat: https://ai.google.dev/gemini-api/docs/text-generation
- Gemini models: https://ai.google.dev/gemini-api/docs/models
- Safety settings: https://ai.google.dev/guide/safety_setting

---

## 1. What You Are Building

You are building a small web app with this flow:

```text
Browser UI
   |
   | POST /api/chat
   v
Node.js Express server
   |
   | Sends short conversation history + latest user message
   v
Gemini API
   |
   | Model reply
   v
Express server stores latest user/model messages
   |
   | JSON response
   v
Browser renders assistant message
```

The browser will never call Gemini directly. This is important because your API key must stay private. The browser talks to your own backend. Your backend talks to Gemini.

The chatbot will remember a few recent messages. For example:

```text
User: My name is Arpan.
Bot: Nice to meet you, Arpan.
User: What is my name?
Bot: Your name is Arpan.
```

But because we intentionally keep only short context, after many messages it may forget old information. That is expected. Short memory keeps cost and latency lower, and prevents your server from sending a huge conversation every turn.

---

## 2. Foundation Concepts

### 2.1 What Is a Foundation Model API?

A foundation model API lets your application send input to a large AI model and receive generated output.

Examples:

- Google Gemini API
- OpenAI API
- Anthropic Claude API
- Mistral API
- Cohere API

In this tutorial we use Gemini, but the architecture is almost the same for other providers:

```text
messages/context + user prompt -> model API -> assistant response
```

### 2.2 What Is Multi-Turn Conversation?

A one-turn chatbot only sees the current user message:

```text
User: What is my name?
```

If that is all the model receives, it cannot know the answer.

A multi-turn chatbot sends recent conversation history too:

```json
[
  {
    "role": "user",
    "parts": [{ "text": "My name is Arpan." }]
  },
  {
    "role": "model",
    "parts": [{ "text": "Nice to meet you, Arpan." }]
  },
  {
    "role": "user",
    "parts": [{ "text": "What is my name?" }]
  }
]
```

Now the model can infer that the answer is "Arpan".

### 2.3 What Is Conversational Context?

Context is the information sent to the model for the current answer.

It usually contains:

- a system instruction
- recent user messages
- recent assistant messages
- the newest user message
- optional memory, documents, tool results, or retrieved knowledge

For this tutorial, short context means:

```text
Keep only the last N user/assistant messages.
```

For example, if `MAX_CONTEXT_MESSAGES = 8`, the server stores at most 8 message objects:

```text
user message 1
assistant message 1
user message 2
assistant message 2
user message 3
assistant message 3
user message 4
assistant message 4
```

That is about 4 conversation turns.

### 2.4 Why Not Send the Entire Conversation Forever?

You usually should not send unlimited history because:

- each request becomes slower
- each request may cost more
- old messages can distract the model
- you may hit model context limits
- users may reveal sensitive information that should not be retained longer than needed

Short context is a practical default for a simple chatbot.

### 2.5 SDK Chat Helper vs Manual Context

The Gemini SDK provides a `chats` helper for multi-turn conversations. That helper is convenient, but for this tutorial we will manage context manually on our server.

Why?

- You need to understand what is actually being sent.
- You can cap history to a short window.
- You can reset or persist sessions yourself.
- You can later swap Gemini for another provider more easily.

Under the hood, multi-turn chat still means sending conversation history to the model.

---

## 3. Prerequisites

Install these first:

- Node.js 18 or newer
- npm
- a Gemini API key from Google AI Studio
- a code editor such as VS Code
- a terminal or PowerShell

Check Node and npm:

```powershell
node -v
npm -v
```

If Node is missing, install it from:

```text
https://nodejs.org/
```

---

## 4. Get a Gemini API Key

1. Open Google AI Studio:

   ```text
   https://aistudio.google.com/
   ```

2. Sign in with your Google account.
3. Create an API key.
4. Copy it.

Do not paste the key into frontend JavaScript. Do not commit it to GitHub.

You will store it in a local `.env` file:

```env
GEMINI_API_KEY=your_key_here
```

---

## 5. Project Setup

Open PowerShell in:

```text
C:\Users\Arpan Mondal\assesment
```

Create a project folder:

```powershell
mkdir gemini-context-chatbot
cd gemini-context-chatbot
```

Initialize Node:

```powershell
npm init -y
```

Tell Node to use ES modules:

```powershell
npm pkg set type=module
```

Install dependencies:

```powershell
npm install express dotenv cors @google/genai
```

Create folders:

```powershell
mkdir public
```

Create these files:

```text
gemini-context-chatbot/
  package.json
  .env
  .gitignore
  server.js
  public/
    index.html
    styles.css
    app.js
```

---

## 6. Add Environment Variables

Create `.env`:

```env
GEMINI_API_KEY=replace_this_with_your_real_key
GEMINI_MODEL=gemini-3.5-flash
PORT=3000
MAX_CONTEXT_MESSAGES=8
```

Meaning:

- `GEMINI_API_KEY`: your private API key
- `GEMINI_MODEL`: the model name
- `PORT`: local server port
- `MAX_CONTEXT_MESSAGES`: how many messages to keep in short context

Create `.gitignore`:

```gitignore
node_modules
.env
npm-debug.log
```

The `.env` file must be ignored because it contains secrets.

---

## 7. Backend: Express Server

Create `server.js`:

```js
import "dotenv/config";
import crypto from "node:crypto";
import cors from "cors";
import express from "express";
import { GoogleGenAI } from "@google/genai";

const app = express();

const PORT = Number(process.env.PORT || 3000);
const MODEL = process.env.GEMINI_MODEL || "gemini-3.5-flash";
const MAX_CONTEXT_MESSAGES = Number(process.env.MAX_CONTEXT_MESSAGES || 8);
const MAX_INPUT_CHARS = 4000;

if (!process.env.GEMINI_API_KEY) {
  console.warn("Missing GEMINI_API_KEY. Add it to your .env file.");
}

const ai = new GoogleGenAI({
  apiKey: process.env.GEMINI_API_KEY,
});

app.use(cors());
app.use(express.json({ limit: "1mb" }));
app.use(express.static("public"));

const sessions = new Map();

function createSessionId() {
  return crypto.randomUUID();
}

function getOrCreateHistory(sessionId) {
  const id =
    typeof sessionId === "string" && sessionId.trim()
      ? sessionId.trim()
      : createSessionId();

  if (!sessions.has(id)) {
    sessions.set(id, []);
  }

  return {
    sessionId: id,
    history: sessions.get(id),
  };
}

function toGeminiContent(message) {
  return {
    role: message.role,
    parts: [{ text: message.text }],
  };
}

function trimHistory(history) {
  while (history.length > MAX_CONTEXT_MESSAGES) {
    history.shift();
  }
}

function publicHistory(history) {
  return history.map((message) => ({
    role: message.role,
    text: message.text,
  }));
}

app.get("/api/health", (req, res) => {
  res.json({
    ok: true,
    model: MODEL,
    maxContextMessages: MAX_CONTEXT_MESSAGES,
  });
});

app.post("/api/chat", async (req, res) => {
  try {
    const { sessionId: incomingSessionId, message } = req.body || {};
    const userText = typeof message === "string" ? message.trim() : "";

    if (!userText) {
      return res.status(400).json({ error: "Message is required." });
    }

    if (userText.length > MAX_INPUT_CHARS) {
      return res.status(400).json({
        error: `Message is too long. Keep it under ${MAX_INPUT_CHARS} characters.`,
      });
    }

    const { sessionId, history } = getOrCreateHistory(incomingSessionId);

    const currentUserMessage = {
      role: "user",
      text: userText,
    };

    const contents = [
      ...history.map(toGeminiContent),
      toGeminiContent(currentUserMessage),
    ];

    const response = await ai.models.generateContent({
      model: MODEL,
      contents,
      config: {
        systemInstruction:
          "You are a helpful, concise chatbot. Use the recent conversation context when it is relevant. If you do not know something, say so plainly.",
        maxOutputTokens: 700,
      },
    });

    const assistantText =
      response.text?.trim() ||
      "I could not generate a response. Please try again.";

    history.push(currentUserMessage);
    history.push({
      role: "model",
      text: assistantText,
    });

    trimHistory(history);

    res.json({
      sessionId,
      reply: assistantText,
      history: publicHistory(history),
    });
  } catch (error) {
    console.error(error);
    res.status(500).json({
      error: "The chatbot failed to respond. Check the server logs.",
    });
  }
});

app.post("/api/reset", (req, res) => {
  const { sessionId } = req.body || {};

  if (typeof sessionId === "string" && sessions.has(sessionId)) {
    sessions.set(sessionId, []);
  }

  res.json({ ok: true });
});

app.listen(PORT, () => {
  console.log(`Chatbot server running at http://localhost:${PORT}`);
});
```

### Backend Explanation

This line loads `.env`:

```js
import "dotenv/config";
```

This creates the Gemini client:

```js
const ai = new GoogleGenAI({
  apiKey: process.env.GEMINI_API_KEY,
});
```

This stores conversation history in memory:

```js
const sessions = new Map();
```

Each session is:

```js
sessionId -> [
  { role: "user", text: "..." },
  { role: "model", text: "..." }
]
```

This converts your internal format into Gemini's expected content format:

```js
function toGeminiContent(message) {
  return {
    role: message.role,
    parts: [{ text: message.text }],
  };
}
```

Gemini roles are:

- `user` for the human
- `model` for the assistant/model reply

This keeps memory short:

```js
function trimHistory(history) {
  while (history.length > MAX_CONTEXT_MESSAGES) {
    history.shift();
  }
}
```

If `MAX_CONTEXT_MESSAGES=8`, the server keeps only 8 messages. Because each turn has a user message and a model message, that means roughly 4 turns.

This creates the full request context:

```js
const contents = [
  ...history.map(toGeminiContent),
  toGeminiContent(currentUserMessage),
];
```

That is the core of multi-turn chat.

---

## 8. Frontend HTML

Create `public/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Context Chat</title>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body>
    <main class="shell">
      <header class="topbar">
        <div>
          <h1>Context Chat</h1>
          <p id="status">Ready</p>
        </div>
        <button id="resetButton" type="button">Reset</button>
      </header>

      <section id="messages" class="messages" aria-live="polite"></section>

      <form id="chatForm" class="composer">
        <textarea
          id="messageInput"
          rows="1"
          placeholder="Type a message"
          autocomplete="off"
        ></textarea>
        <button id="sendButton" type="submit">Send</button>
      </form>
    </main>

    <script src="/app.js"></script>
  </body>
</html>
```

---

## 9. Frontend CSS

Create `public/styles.css`:

```css
:root {
  color-scheme: light;
  --bg: #f6f7f9;
  --panel: #ffffff;
  --text: #18202a;
  --muted: #667085;
  --line: #d9dee7;
  --user: #0f766e;
  --user-text: #ffffff;
  --assistant: #ffffff;
  --assistant-border: #d9dee7;
  --focus: #2563eb;
  --danger: #b42318;
}

* {
  box-sizing: border-box;
}

html,
body {
  height: 100%;
}

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family:
    Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI",
    sans-serif;
}

button,
textarea {
  font: inherit;
}

.shell {
  width: min(920px, 100%);
  height: 100dvh;
  margin: 0 auto;
  display: grid;
  grid-template-rows: auto 1fr auto;
  background: var(--panel);
  border-left: 1px solid var(--line);
  border-right: 1px solid var(--line);
}

.topbar {
  min-height: 76px;
  padding: 16px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid var(--line);
}

.topbar h1 {
  margin: 0;
  font-size: 20px;
  line-height: 1.2;
  letter-spacing: 0;
}

.topbar p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 14px;
}

.topbar button,
.composer button {
  border: 0;
  border-radius: 8px;
  cursor: pointer;
  font-weight: 650;
}

.topbar button {
  min-width: 76px;
  min-height: 40px;
  padding: 0 14px;
  color: var(--text);
  background: #eef1f5;
}

.topbar button:hover {
  background: #e4e8ef;
}

.messages {
  overflow-y: auto;
  padding: 24px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.message {
  width: fit-content;
  max-width: min(680px, 88%);
  padding: 12px 14px;
  border-radius: 8px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.message.user {
  align-self: flex-end;
  background: var(--user);
  color: var(--user-text);
}

.message.model,
.message.system {
  align-self: flex-start;
  background: var(--assistant);
  border: 1px solid var(--assistant-border);
}

.message.system {
  color: var(--muted);
}

.message.error {
  border-color: #f3b3ad;
  color: var(--danger);
}

.composer {
  padding: 14px;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 10px;
  border-top: 1px solid var(--line);
  background: #fbfcfe;
}

.composer textarea {
  width: 100%;
  max-height: 180px;
  resize: none;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px 13px;
  outline: none;
  line-height: 1.45;
  color: var(--text);
  background: #ffffff;
}

.composer textarea:focus {
  border-color: var(--focus);
  box-shadow: 0 0 0 3px rgb(37 99 235 / 14%);
}

.composer button {
  min-width: 86px;
  min-height: 46px;
  padding: 0 18px;
  color: white;
  background: #2563eb;
}

.composer button:hover {
  background: #1d4ed8;
}

.composer button:disabled,
.topbar button:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

@media (max-width: 640px) {
  .shell {
    border: 0;
  }

  .topbar {
    padding: 14px;
  }

  .messages {
    padding: 18px 14px;
  }

  .message {
    max-width: 94%;
  }

  .composer {
    grid-template-columns: 1fr;
  }

  .composer button {
    width: 100%;
  }
}
```

---

## 10. Frontend JavaScript

Create `public/app.js`:

```js
const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const messages = document.querySelector("#messages");
const sendButton = document.querySelector("#sendButton");
const resetButton = document.querySelector("#resetButton");
const statusText = document.querySelector("#status");

const SESSION_KEY = "context_chat_session_id";

let sessionId =
  localStorage.getItem(SESSION_KEY) ||
  (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()));

localStorage.setItem(SESSION_KEY, sessionId);

function setStatus(text) {
  statusText.textContent = text;
}

function addMessage(role, text, options = {}) {
  const bubble = document.createElement("div");
  bubble.className = `message ${role}${options.error ? " error" : ""}`;
  bubble.textContent = text;
  messages.appendChild(bubble);
  messages.scrollTop = messages.scrollHeight;
  return bubble;
}

function setBusy(isBusy) {
  sendButton.disabled = isBusy;
  resetButton.disabled = isBusy;
  input.disabled = isBusy;
}

function autoResizeInput() {
  input.style.height = "auto";
  input.style.height = `${input.scrollHeight}px`;
}

async function sendMessage(message) {
  setBusy(true);
  setStatus("Thinking");

  addMessage("user", message);
  input.value = "";
  autoResizeInput();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        sessionId,
        message,
      }),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || "Request failed.");
    }

    sessionId = data.sessionId;
    localStorage.setItem(SESSION_KEY, sessionId);

    addMessage("model", data.reply);
    setStatus("Ready");
  } catch (error) {
    addMessage("system", error.message, { error: true });
    setStatus("Error");
  } finally {
    setBusy(false);
    input.focus();
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();

  if (!message) {
    return;
  }

  await sendMessage(message);
});

input.addEventListener("input", autoResizeInput);

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

resetButton.addEventListener("click", async () => {
  await fetch("/api/reset", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ sessionId }),
  });

  sessionId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
  localStorage.setItem(SESSION_KEY, sessionId);
  messages.textContent = "";
  addMessage("system", "Conversation reset.");
  setStatus("Ready");
  input.focus();
});

addMessage("system", "Ask me something.");
input.focus();
```

---

## 11. Run the Chatbot

Start the server:

```powershell
npm run start
```

Your default `package.json` may not have a `start` script yet. Add one:

```powershell
npm pkg set scripts.start="node server.js"
npm pkg set scripts.dev="node --watch server.js"
```

Now run:

```powershell
npm run dev
```

Open:

```text
http://localhost:3000
```

Try this test:

```text
My name is Arpan and I am building a chatbot.
```

Then:

```text
What am I building?
```

The bot should answer that you are building a chatbot, because that message is still in short context.

Now send many more messages. Eventually ask:

```text
What is my name?
```

If the original name message fell out of the short context window, the bot may not remember. That proves your context trimming is working.

---

## 12. Debugging the API Call

Check the health route:

```text
http://localhost:3000/api/health
```

Expected response:

```json
{
  "ok": true,
  "model": "gemini-3.5-flash",
  "maxContextMessages": 8
}
```

If the chat does not work, check your terminal.

Common errors:

### Missing API Key

You may see:

```text
Missing GEMINI_API_KEY. Add it to your .env file.
```

Fix `.env`:

```env
GEMINI_API_KEY=your_real_key
```

Restart the server after changing `.env`.

### Model Not Found

If the API says the model is unavailable, change:

```env
GEMINI_MODEL=gemini-2.5-flash
```

Then restart:

```powershell
npm run dev
```

Also check:

```text
https://ai.google.dev/gemini-api/docs/models
```

### Port Already in Use

If port `3000` is busy:

```env
PORT=3001
```

Restart and open:

```text
http://localhost:3001
```

### Browser Shows a Generic Error

Open DevTools:

- Chrome/Edge: `F12`
- go to the `Console` tab
- go to the `Network` tab
- inspect the `/api/chat` request

The server terminal usually has the more useful error.

---

## 13. How the Short Context Works

The most important state is here:

```js
const sessions = new Map();
```

This is in-memory storage. It disappears when the server restarts.

A session looks like:

```js
[
  { role: "user", text: "My name is Arpan." },
  { role: "model", text: "Nice to meet you, Arpan." },
  { role: "user", text: "What is my name?" },
  { role: "model", text: "Your name is Arpan." }
]
```

When the browser sends a new message, it includes:

```json
{
  "sessionId": "some-id",
  "message": "What is my name?"
}
```

The server finds that session's history:

```js
const { sessionId, history } = getOrCreateHistory(incomingSessionId);
```

Then it sends the model:

```js
const contents = [
  ...history.map(toGeminiContent),
  toGeminiContent(currentUserMessage),
];
```

After Gemini replies, the server appends:

```js
history.push(currentUserMessage);
history.push({
  role: "model",
  text: assistantText,
});
```

Then the server trims:

```js
trimHistory(history);
```

That is the whole short-term memory system.

---

## 14. Basic Version: One File API Test

Before building the UI, you can test Gemini with one small script.

Create `quick-test.js`:

```js
import "dotenv/config";
import { GoogleGenAI } from "@google/genai";

const ai = new GoogleGenAI({
  apiKey: process.env.GEMINI_API_KEY,
});

const response = await ai.models.generateContent({
  model: process.env.GEMINI_MODEL || "gemini-3.5-flash",
  contents: "Explain multi-turn chatbot context in two sentences.",
});

console.log(response.text);
```

Run:

```powershell
node quick-test.js
```

If this works, your API key and SDK are configured correctly.

---

## 15. Intermediate Version: Use the Gemini Chat Helper

The official SDK also supports a chat helper:

```js
import "dotenv/config";
import { GoogleGenAI } from "@google/genai";

const ai = new GoogleGenAI({
  apiKey: process.env.GEMINI_API_KEY,
});

const chat = ai.chats.create({
  model: "gemini-3.5-flash",
});

let response = await chat.sendMessage({
  message: "I have 2 dogs in my house.",
});

console.log(response.text);

response = await chat.sendMessage({
  message: "How many paws are in my house?",
});

console.log(response.text);
```

This is very convenient for scripts. For a web server, manual history management is often clearer because:

- you decide how much history to keep
- you can store sessions in a database
- you can inspect exactly what the model sees
- you can apply your own privacy rules

---

## 16. Advanced Upgrade 1: Streaming Responses

The current app waits for the full model response before showing it. Streaming makes the assistant feel faster by sending chunks as they arrive.

There are several ways to stream:

- Server-Sent Events
- WebSockets
- HTTP chunked responses
- fetch streams

For a simple chatbot, Server-Sent Events are easy.

### 16.1 Add a Streaming Endpoint

Add this to `server.js`:

```js
app.post("/api/chat/stream", async (req, res) => {
  try {
    const { sessionId: incomingSessionId, message } = req.body || {};
    const userText = typeof message === "string" ? message.trim() : "";

    if (!userText) {
      return res.status(400).json({ error: "Message is required." });
    }

    const { sessionId, history } = getOrCreateHistory(incomingSessionId);
    const currentUserMessage = { role: "user", text: userText };

    const contents = [
      ...history.map(toGeminiContent),
      toGeminiContent(currentUserMessage),
    ];

    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });

    res.write(`event: session\n`);
    res.write(`data: ${JSON.stringify({ sessionId })}\n\n`);

    const stream = await ai.models.generateContentStream({
      model: MODEL,
      contents,
      config: {
        systemInstruction:
          "You are a helpful, concise chatbot. Use recent context when relevant.",
        maxOutputTokens: 700,
      },
    });

    let assistantText = "";

    for await (const chunk of stream) {
      const text = chunk.text || "";
      assistantText += text;
      res.write(`event: chunk\n`);
      res.write(`data: ${JSON.stringify({ text })}\n\n`);
    }

    const finalText =
      assistantText.trim() || "I could not generate a response.";

    history.push(currentUserMessage);
    history.push({ role: "model", text: finalText });
    trimHistory(history);

    res.write(`event: done\n`);
    res.write(`data: ${JSON.stringify({ ok: true })}\n\n`);
    res.end();
  } catch (error) {
    console.error(error);
    res.write(`event: error\n`);
    res.write(
      `data: ${JSON.stringify({ error: "Streaming response failed." })}\n\n`,
    );
    res.end();
  }
});
```

However, there is a catch: browser `EventSource` only supports `GET`, not `POST`. Since chat messages should be sent with `POST`, you can either:

- use `fetch` and parse the stream manually
- use a two-step flow where `POST` creates a stream ID and `GET` consumes it
- use WebSockets

For beginners, keep the non-streaming version first. Add streaming after the normal chatbot works.

### 16.2 Simpler Fetch Stream Pattern

A more practical streaming endpoint can return plain text chunks instead of SSE. The server writes chunks, and the browser reads them.

The endpoint:

```js
app.post("/api/chat/text-stream", async (req, res) => {
  try {
    const { sessionId: incomingSessionId, message } = req.body || {};
    const userText = typeof message === "string" ? message.trim() : "";

    if (!userText) {
      return res.status(400).send("Message is required.");
    }

    const { history } = getOrCreateHistory(incomingSessionId);
    const currentUserMessage = { role: "user", text: userText };

    const contents = [
      ...history.map(toGeminiContent),
      toGeminiContent(currentUserMessage),
    ];

    res.setHeader("Content-Type", "text/plain; charset=utf-8");
    res.setHeader("Cache-Control", "no-cache");

    const stream = await ai.models.generateContentStream({
      model: MODEL,
      contents,
      config: {
        systemInstruction:
          "You are a helpful, concise chatbot. Use recent context when relevant.",
        maxOutputTokens: 700,
      },
    });

    let assistantText = "";

    for await (const chunk of stream) {
      const text = chunk.text || "";
      assistantText += text;
      res.write(text);
    }

    const finalText =
      assistantText.trim() || "I could not generate a response.";

    history.push(currentUserMessage);
    history.push({ role: "model", text: finalText });
    trimHistory(history);
    res.end();
  } catch (error) {
    console.error(error);
    res.status(500).send("Streaming failed.");
  }
});
```

The browser can read it:

```js
const response = await fetch("/api/chat/text-stream", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
  },
  body: JSON.stringify({ sessionId, message }),
});

const reader = response.body.getReader();
const decoder = new TextDecoder();
const bubble = addMessage("model", "");

while (true) {
  const { value, done } = await reader.read();

  if (done) {
    break;
  }

  bubble.textContent += decoder.decode(value, { stream: true });
  messages.scrollTop = messages.scrollHeight;
}
```

Streaming is not required for the assignment, but it is a strong advanced improvement.

---

## 17. Advanced Upgrade 2: Persistent Conversations

The current server stores sessions in a JavaScript `Map`. That is fine for learning, but it has limitations:

- memory disappears on restart
- multiple server instances will not share memory
- no user authentication
- no analytics
- no cleanup policy beyond process lifetime

For persistence, use a database.

Options:

- SQLite for local/small apps
- PostgreSQL for production
- MongoDB for document-style storage
- Redis for short-lived session memory

### 17.1 Simple Database Schema

For SQL:

```sql
CREATE TABLE conversations (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL,
  role TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);
```

When a user sends a message:

1. find or create conversation
2. load last `N` messages
3. call Gemini
4. insert user message
5. insert assistant message

### 17.2 Why Load Only the Last N Messages?

Even if the database stores the full conversation, you do not have to send the full conversation to the model.

You can store everything but send only:

```sql
SELECT role, text
FROM messages
WHERE conversation_id = ?
ORDER BY id DESC
LIMIT 8;
```

Then reverse the result before sending to Gemini so the oldest of the selected messages comes first.

---

## 18. Advanced Upgrade 3: Summarized Long-Term Memory

Short context is good, but sometimes you want the bot to remember important facts from older messages.

A practical pattern is:

```text
short recent messages + compact conversation summary
```

Example:

```text
Summary:
The user is Arpan. They are building a Gemini chatbot with Node.js and want simple explanations.

Recent messages:
User: How do I deploy it?
Assistant: You can deploy it on Render...
User: Can I use Vercel?
```

The model sees both:

- long-term summary
- recent exact messages

### 18.1 Add a Summary Field

Store this per session:

```js
const sessions = new Map();

// session value:
{
  summary: "",
  history: []
}
```

### 18.2 Summarize When History Gets Too Long

When history exceeds the limit, ask Gemini to update the summary:

```js
async function summarizeConversation(oldSummary, messagesToSummarize) {
  const transcript = messagesToSummarize
    .map((message) => `${message.role}: ${message.text}`)
    .join("\n");

  const response = await ai.models.generateContent({
    model: MODEL,
    contents: `
Update the conversation summary.

Existing summary:
${oldSummary || "(none)"}

New transcript:
${transcript}

Return a compact summary of stable facts, user preferences, decisions, and unresolved tasks.
Do not include unimportant small talk.
`,
    config: {
      maxOutputTokens: 300,
    },
  });

  return response.text?.trim() || oldSummary || "";
}
```

Then include the summary in the system instruction:

```js
const systemInstruction = `
You are a helpful, concise chatbot.

Conversation summary:
${session.summary || "(No older summary yet.)"}

Use the summary only when relevant. Recent messages are more reliable than the summary.
`;
```

This gives you better memory without sending everything.

### 18.3 Be Careful with Summaries

Summaries can become wrong. To reduce errors:

- summarize only stable facts
- prefer recent exact messages over summary
- let users reset memory
- avoid storing sensitive data unless necessary
- show or export memory if your app is user-facing

---

## 19. Advanced Upgrade 4: Token-Based Context Instead of Message Count

The tutorial uses message count because it is easy:

```js
MAX_CONTEXT_MESSAGES=8
```

But message count is not the same as token count.

One message can be tiny:

```text
yes
```

Another can be huge:

```text
Here is my 30-page essay...
```

A production app should use a token budget.

Basic strategy:

```text
Start from newest message and move backward until you reach a rough token/character limit.
```

Approximate tokens by characters:

```js
function approximateTokens(text) {
  return Math.ceil(text.length / 4);
}
```

Build history under a budget:

```js
function selectHistoryByTokenBudget(history, maxTokens) {
  const selected = [];
  let used = 0;

  for (let index = history.length - 1; index >= 0; index -= 1) {
    const message = history[index];
    const tokens = approximateTokens(message.text);

    if (used + tokens > maxTokens) {
      break;
    }

    selected.push(message);
    used += tokens;
  }

  return selected.reverse();
}
```

Then:

```js
const shortHistory = selectHistoryByTokenBudget(history, 2000);
```

This is more flexible than keeping exactly 8 messages.

---

## 20. Advanced Upgrade 5: Add Safety Settings

Gemini has built-in safety behavior, and the API supports adjustable safety settings. For many simple apps, defaults are enough. If your app has a specific audience, review the official safety settings docs.

Example:

```js
const response = await ai.models.generateContent({
  model: MODEL,
  contents,
  config: {
    systemInstruction:
      "You are a helpful chatbot. Avoid unsafe instructions and be clear about limitations.",
    safetySettings: [
      {
        category: "HARM_CATEGORY_HARASSMENT",
        threshold: "BLOCK_LOW_AND_ABOVE",
      },
      {
        category: "HARM_CATEGORY_HATE_SPEECH",
        threshold: "BLOCK_LOW_AND_ABOVE",
      },
    ],
  },
});
```

For production, also add your own application rules:

- do not allow users to submit extremely long messages
- do not display raw errors to users
- log abuse patterns carefully
- consider moderation for public apps
- provide a report/reset option

---

## 21. Advanced Upgrade 6: Rate Limiting

Without rate limiting, one user can spam your endpoint.

Install:

```powershell
npm install express-rate-limit
```

Add to `server.js`:

```js
import rateLimit from "express-rate-limit";

const chatLimiter = rateLimit({
  windowMs: 60 * 1000,
  limit: 20,
  standardHeaders: true,
  legacyHeaders: false,
});

app.use("/api/chat", chatLimiter);
```

This allows 20 chat requests per minute per IP.

Tune the number for your use case.

---

## 22. Advanced Upgrade 7: Better Prompting

The system instruction controls assistant behavior:

```js
systemInstruction:
  "You are a helpful, concise chatbot. Use the recent conversation context when it is relevant. If you do not know something, say so plainly."
```

You can customize it.

For a study assistant:

```text
You are a patient study assistant. Ask one clarifying question when needed. Prefer examples. Do not solve homework completely unless the user asks for a worked solution.
```

For a coding assistant:

```text
You are a practical coding assistant. Give runnable examples. Mention assumptions. If code may be unsafe or destructive, warn the user first.
```

For a customer support bot:

```text
You are a support assistant for ACME Store. Be concise and polite. If the user asks about refunds, explain the policy. If account access is needed, tell the user to contact human support.
```

Good system instructions:

- define role
- define tone
- define boundaries
- define when to ask clarifying questions
- define what not to do

Avoid giant prompts full of conflicting rules.

---

## 23. Advanced Upgrade 8: Deployment

You can deploy this app to services such as:

- Render
- Railway
- Fly.io
- Google Cloud Run
- Azure App Service
- AWS Elastic Beanstalk

The important deployment rules:

- set `GEMINI_API_KEY` as an environment variable in the hosting dashboard
- do not upload `.env`
- use `npm start`
- make sure the app listens on `process.env.PORT`

You already do this:

```js
const PORT = Number(process.env.PORT || 3000);
```

Production `package.json` scripts:

```json
{
  "scripts": {
    "start": "node server.js",
    "dev": "node --watch server.js"
  }
}
```

### 23.1 Render Example

1. Push your project to GitHub.
2. Create a new Web Service on Render.
3. Connect the GitHub repo.
4. Build command:

   ```text
   npm install
   ```

5. Start command:

   ```text
   npm start
   ```

6. Add environment variables:

   ```text
   GEMINI_API_KEY=your_key
   GEMINI_MODEL=gemini-3.5-flash
   MAX_CONTEXT_MESSAGES=8
   ```

7. Deploy.

---

## 24. Security Checklist

Before sharing your chatbot:

- API key is only in `.env` or hosting environment variables
- `.env` is in `.gitignore`
- browser never receives the API key
- server validates empty messages
- server limits message length
- server has rate limiting
- errors shown to users are generic
- sensitive logs are avoided
- reset conversation is available
- dependencies are updated
- production app uses HTTPS

---

## 25. Privacy Checklist

For a real user-facing chatbot:

- tell users if conversations are stored
- explain how long memory is kept
- let users reset or delete conversations
- avoid storing unnecessary personal data
- avoid logging full conversations unless needed
- protect database access
- add authentication if users can access saved chats

The tutorial app stores conversations only in server memory and loses them on restart. That is useful for learning, but not enough for a full production product.

---

## 26. Common Mistakes

### Mistake 1: Calling Gemini Directly from Browser

Do not do this:

```js
const ai = new GoogleGenAI({ apiKey: "secret-key-in-browser" });
```

Anyone can open DevTools and steal the key.

Correct approach:

```text
Browser -> your backend -> Gemini
```

### Mistake 2: Forgetting to Send History

If you send only:

```js
contents: "What is my name?"
```

the model has no conversation memory.

Send:

```js
contents: [
  ...history.map(toGeminiContent),
  toGeminiContent(currentUserMessage),
]
```

### Mistake 3: Keeping Unlimited History

Unlimited memory seems useful, but it becomes slow, expensive, and noisy.

Use:

```js
trimHistory(history);
```

### Mistake 4: Trusting the Model Too Much

Models can be wrong. For factual, medical, legal, financial, or high-risk topics:

- add disclaimers
- ground answers in trusted sources
- show sources when possible
- route critical cases to humans

### Mistake 5: No Reset Button

A reset button is simple and important. It lets the user clear bad context.

---

## 27. Final Complete File List

Your project should look like this:

```text
gemini-context-chatbot/
  .env
  .gitignore
  package.json
  server.js
  public/
    index.html
    styles.css
    app.js
```

Run:

```powershell
npm run dev
```

Open:

```text
http://localhost:3000
```

---

## 28. Assignment-Style Explanation

If you need to explain this project in an assessment, you can say:

```text
I built a browser-based chatbot using Node.js, Express, and the Gemini API.
The frontend sends user messages to my backend instead of calling the model directly, which protects the API key.
The backend keeps a session-specific conversation history in memory.
For each new message, it sends Gemini the recent history plus the latest user message.
To maintain short conversational context, the backend trims each session to a fixed number of recent messages.
The UI supports sending messages, displaying assistant replies, and resetting the conversation.
```

Shorter version:

```text
The chatbot supports multi-turn conversation by storing recent user and model messages per session and sending that short history with every Gemini API request.
```

---

## 29. Practice Tasks

Try these after the basic app works:

1. Change `MAX_CONTEXT_MESSAGES` from `8` to `4`.
2. Ask the bot to remember your favorite programming language.
3. Send enough messages to push that fact out of context.
4. Ask for the favorite language again.
5. Add a token-budget context selector.
6. Add streaming responses.
7. Add SQLite persistence.
8. Add a dropdown to choose between short, medium, and long context.
9. Add a system prompt editor on the server side.
10. Deploy the app.

---

## 30. Optional: Provider-Agnostic Architecture

If you later want to support multiple model providers, wrap model calls in a service function.

Create:

```text
services/modelClient.js
```

Example:

```js
import { GoogleGenAI } from "@google/genai";

const ai = new GoogleGenAI({
  apiKey: process.env.GEMINI_API_KEY,
});

export async function generateChatReply({ model, contents, systemInstruction }) {
  const response = await ai.models.generateContent({
    model,
    contents,
    config: {
      systemInstruction,
      maxOutputTokens: 700,
    },
  });

  return response.text?.trim() || "";
}
```

Then `server.js` calls:

```js
const assistantText = await generateChatReply({
  model: MODEL,
  contents,
  systemInstruction:
    "You are a helpful, concise chatbot. Use recent context when relevant.",
});
```

Later you can create:

```text
services/openaiClient.js
services/anthropicClient.js
services/geminiClient.js
```

and choose the provider with:

```env
MODEL_PROVIDER=gemini
```

This is an advanced design, but it keeps your code clean as the app grows.

---

## 31. Summary

You now have a complete path from basic to advanced:

- a one-file Gemini API test
- a Node/Express backend
- a simple browser chat UI
- session-based multi-turn conversation
- short context trimming
- reset behavior
- debugging steps
- streaming upgrade path
- database persistence plan
- summarization memory pattern
- rate limiting and security checklist
- deployment notes

The core idea is simple:

```text
Every turn, send the model the newest user message plus enough recent conversation to answer well.
```

That is the heart of a multi-turn chatbot.
