require('dotenv').config();
const express = require('express');
const path = require('path');

const app = express();
const PORT = 3000;
const AI_URL = 'http://localhost:8000';

app.use(express.json());
app.use(express.static(path.join(__dirname)));

app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.header('Access-Control-Allow-Headers', 'Content-Type, X-User-Id');
  if (req.method === 'OPTIONS') return res.sendStatus(200);
  next();
});

async function proxyToAI(targetPath, req, res) {
  console.log('Proxying to:', `${AI_URL}${targetPath}`, req.body);
  try {
    const headers = { 'Content-Type': 'application/json' };
    if (req.headers['x-user-id']) {
      headers['X-User-Id'] = req.headers['x-user-id'];
    }

    const response = await fetch(`${AI_URL}${targetPath}`, {
      method: 'POST',
      headers,
      body: JSON.stringify(req.body)
    });

    const data = await response.json();
    if (!response.ok) {
      return res.status(response.status).json({ error: data.detail || 'AI service error' });
    }
    res.json(data);
  } catch (err) {
    console.error(`Proxy error for ${targetPath}:`, err.message);
    res.status(500).json({ error: 'Could not reach AI service. Is Python running?' });
  }
}

// routes
app.post('/generate',       (req, res) => proxyToAI('/generate',       req, res));
app.post('/improve-prompt', (req, res) => proxyToAI('/improve-prompt', req, res));
app.post('/suggest-edits',  (req, res) => proxyToAI('/suggest-edits',  req, res));
app.post('/reset',          (req, res) => proxyToAI('/reset',          req, res));

app.listen(PORT, () => {
  console.log(`Node server running at http://localhost:${PORT}`);
  console.log(`Proxying AI requests to ${AI_URL}`);
});