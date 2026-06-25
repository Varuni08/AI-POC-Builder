**AI POC Builder**



A chat-driven proof-of-concept builder that converts natural-language prompts into responsive HTML web pages with live preview and iterative editing.



**Features**



\* Natural-language-to-HTML generation

\* Live preview and code view

\* Chat-based iterative editing

\* Persistent project context

\* Deterministic handling for common CSS changes

\* OpenAI and Ollama/Gemma model routing

\* Prompt improvement and history summarisation

\* Cost monitoring and rate limiting



**Tech Stack**



Python, FastAPI, JavaScript, Node.js, Express.js, HTML, CSS, Tailwind CSS, OpenAI API, Ollama and Gemma.



**Project Structure**



\* `ai/` : AI generation, editing, context and caching logic

\* `index.html` : Frontend application

\* `server.js` : Node.js proxy server

\* `requirements.txt` : Python dependencies

\* `package.json` : Node.js dependencies

\* `.env.example` : Environment variable template



**Setup**



Install Python dependencies:



```bash

pip install -r requirements.txt

```



Install Node.js dependencies:



```bash

npm install

```



Create a `.env` file using `.env.example` and add your OpenAI API key.



\## Note



Originally developed as part of my work at CodeWork AI and published on my personal GitHub with permission.



