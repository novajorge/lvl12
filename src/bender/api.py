"""HTTP API endpoints — FastAPI routes for external triggers."""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Security, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from bender.claude_code import ClaudeCodeError, invoke_claude, invoke_claude_streaming
from bender.config import Settings
from bender.job_tracker import JobTracker, JobStatus
from bender.session_manager import SessionManager
from bender.slack_utils import SLACK_MSG_LIMIT, LONG_RESPONSE_THRESHOLD, md_to_mrkdwn, split_text, create_temp_file

logger = logging.getLogger(__name__)

security = HTTPBearer()

# Dashboard HTML template
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bender Job Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #eee; min-height: 100vh; display: flex; }
        .sidebar { width: 220px; background: #0f0f23; min-height: 100vh; padding: 20px 0; position: fixed; left: 0; top: 0; bottom: 0; }
        .sidebar-logo { padding: 0 20px 20px; border-bottom: 1px solid #333; margin-bottom: 15px; }
        .sidebar-logo h2 { color: #00d4ff; font-size: 20px; }
        .sidebar-logo span { color: #666; font-size: 12px; }
        .sidebar-nav { list-style: none; }
        .sidebar-nav li { }
        .sidebar-nav a { display: flex; align-items: center; gap: 10px; padding: 12px 20px; color: #888; text-decoration: none; transition: all 0.2s; border-left: 3px solid transparent; }
        .sidebar-nav a:hover { background: #1a1a2e; color: #eee; }
        .sidebar-nav a.active { background: #16213e; color: #00d4ff; border-left-color: #00d4ff; }
        .sidebar-nav .nav-icon { font-size: 18px; width: 24px; text-align: center; }
        .main-content { flex: 1; margin-left: 220px; padding: 20px 30px; max-width: calc(100% - 220px); }
        .container { max-width: 100%; }
        h1 { color: #00d4ff; margin-bottom: 20px; }
        .section { display: none; }
        .section.active { display: block; }
        .controls { display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; align-items: center; }
        .controls input, .controls select { padding: 8px 12px; border: 1px solid #333; border-radius: 4px; background: #16213e; color: #eee; }
        .controls input { flex: 1; min-width: 200px; }
        .controls button { padding: 8px 16px; background: #00d4ff; color: #1a1a2e; border: none; border-radius: 4px; cursor: pointer; font-weight: 600; }
        .controls button:hover { background: #00b8e6; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .stat-card { background: #16213e; padding: 15px; border-radius: 8px; text-align: center; }
        .stat-card .value { font-size: 28px; font-weight: bold; color: #00d4ff; }
        .stat-card .label { font-size: 12px; color: #888; text-transform: uppercase; }
        table { width: 100%; border-collapse: collapse; background: #16213e; border-radius: 8px; overflow: hidden; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #333; }
        th { background: #0f0f23; color: #00d4ff; font-weight: 600; font-size: 12px; text-transform: uppercase; }
        tr:hover { background: #1f2b4d; }
        .status { padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }
        .status.pending { background: #ffd700; color: #1a1a2e; }
        .status.running { background: #00d4ff; color: #1a1a2e; }
        .status.completed { background: #00ff88; color: #1a1a2e; }
        .status.failed { background: #ff4757; color: #fff; }
        .message-cell { max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .detail-row td { background: #1f2b4d; }
        .detail-content { padding: 15px; color: #aaa; font-size: 13px; }
        .detail-content pre { background: #0f0f23; padding: 10px; border-radius: 4px; overflow-x: auto; white-space: pre-wrap; word-break: break-all; max-height: 300px; }
        .detail-content .error { color: #ff4757; }
        .detail-content .metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 10px; }
        .detail-content .metric { background: #0f0f23; padding: 10px; border-radius: 4px; text-align: center; }
        .detail-content .metric-value { font-size: 18px; color: #00d4ff; }
        .detail-content .metric-label { font-size: 11px; color: #666; }
        .timestamp { color: #666; font-size: 12px; }
        .refresh-info { text-align: center; color: #666; font-size: 12px; margin-top: 20px; }
        .charts-container { display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 20px; margin-bottom: 20px; }
        .chart-box { background: #16213e; border-radius: 8px; padding: 15px; }
        .chart-box h3 { color: #00d4ff; font-size: 14px; margin-bottom: 15px; }
        .console { background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 12px; margin-top: 10px; font-family: 'Consolas', 'Monaco', 'Courier New', monospace; font-size: 12px; max-height: 250px; overflow-y: auto; color: #c9d1d9; }
        .console-line { padding: 1px 0; }
        .console-line.thinking { color: #f0c674; }
        .console-line.tool_start { color: #81a2be; }
        .console-line.tool_end { color: #b5bd68; }
        .console-line.progress { color: #8abeb7; }
        .console-line.terminal { color: #b5bd68; }
        .console-line.error { color: #cc6666; }
        .console-time { color: #5c6370; margin-right: 8px; font-size: 11px; }
        .console-prompt { color: #b5bd68; margin-right: 5px; }
        .commits-list { background: #16213e; border-radius: 8px; overflow: hidden; }
        .commit-item { padding: 12px 15px; border-bottom: 1px solid #333; display: grid; grid-template-columns: 150px 80px 1fr 180px 150px; align-items: center; gap: 15px; }
        .commit-item:last-child { border-bottom: none; }
        .commit-item:hover { background: #1f2b4d; }
        .commit-project { font-weight: 600; color: #ffd700; font-size: 12px; }
        .commit-hash { font-family: monospace; color: #00d4ff; background: #0f0f23; padding: 3px 8px; border-radius: 4px; font-size: 12px; text-decoration: none; }
        .commit-hash:hover { background: #1a1a2e; }
        .commit-message { color: #eee; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .commit-meta { color: #666; font-size: 12px; display: flex; flex-direction: column; }
        .commit-author { color: #00ff88; }
        .commit-date { font-size: 11px; }
        .skills-section { margin-top: 30px; }
        .skills-tabs { display: flex; gap: 5px; margin-bottom: 15px; border-bottom: 1px solid #333; }
        .skills-tab { padding: 10px 20px; background: transparent; border: none; color: #888; cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -1px; }
        .skills-tab:hover { color: #eee; }
        .skills-tab.active { color: #00d4ff; border-bottom-color: #00d4ff; }
        .skills-content { background: #16213e; border-radius: 8px; padding: 15px; }
        .skills-content.hidden { display: none; }
        .skills-editor { width: 100%; min-height: 300px; background: #0a0a14; color: #eee; border: 1px solid #333; border-radius: 4px; padding: 10px; font-family: monospace; font-size: 13px; resize: vertical; }
        .skills-save { margin-top: 10px; padding: 8px 20px; background: #00d4ff; color: #1a1a2e; border: none; border-radius: 4px; cursor: pointer; font-weight: 600; }
        .skills-save:hover { background: #00b8e6; }
        .skills-save:disabled { background: #555; cursor: not-allowed; }
        .skills-list { display: grid; gap: 10px; }
        .skill-item { background: #0f0f23; padding: 10px 15px; border-radius: 4px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; }
        .skill-item:hover { background: #1a1a2e; }
        .skill-item-name { color: #00d4ff; font-weight: 600; }
        .skill-item-badge { background: #333; padding: 2px 8px; border-radius: 4px; font-size: 11px; color: #888; }
        .save-message { margin-left: 10px; color: #00ff88; font-size: 13px; }
        .sessions-list { background: #16213e; border-radius: 8px; overflow: hidden; }
        .session-item { padding: 12px 15px; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; }
        .session-item:last-child { border-bottom: none; }
        .session-item:hover { background: #1f2b4d; }
        .session-info { display: flex; gap: 20px; align-items: center; }
        .session-thread { color: #ffd700; font-family: monospace; font-size: 12px; }
        .session-id { color: #888; font-family: monospace; font-size: 11px; }
        .btn-abort { padding: 5px 12px; background: #ff4757; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px; }
        .btn-abort:hover { background: #ff6b81; }
    </style>
</head>
<body>
    <nav class="sidebar">
        <div class="sidebar-logo">
            <h2>Bender</h2>
            <span>Job Dashboard</span>
        </div>
        <ul class="sidebar-nav">
            <li><a href="#" class="nav-item active" data-section="overview"><span class="nav-icon">📊</span> Overview</a></li>
            <li><a href="#" class="nav-item" data-section="jobs"><span class="nav-icon">📋</span> Jobs</a></li>
            <li><a href="#" class="nav-item" data-section="commits"><span class="nav-icon">🔧</span> Commits</a></li>
            <li><a href="#" class="nav-item" data-section="sessions"><span class="nav-icon">💬</span> Sessions</a></li>
            <li><a href="#" class="nav-item" data-section="console"><span class="nav-icon">💻</span> Console</a></li>
            <li><a href="#" class="nav-item" data-section="config"><span class="nav-icon">⚙️</span> Configuration</a></li>
        </ul>
    </nav>
    <div class="main-content">
        <div id="section-overview" class="section active">
            <h1 style="margin-bottom: 20px;">Overview</h1>
            <div class="stats" id="stats"></div>
            <div class="charts-container">
                <div class="chart-box">
                    <h3>Monthly Requests</h3>
                    <canvas id="requestsChart"></canvas>
                </div>
                <div class="chart-box">
                    <h3>Monthly Costs (USD)</h3>
                    <canvas id="costsChart"></canvas>
                </div>
                <div class="chart-box">
                    <h3>Token Usage</h3>
                    <canvas id="tokensChart"></canvas>
                </div>
            </div>
        </div>

        <div id="section-jobs" class="section">
            <h1 style="margin-bottom: 20px;">Jobs</h1>
            <div class="controls">
                <input type="text" id="search" placeholder="Search messages..." oninput="loadJobs()">
                <select id="statusFilter" onchange="loadJobs()">
                    <option value="">All Statuses</option>
                    <option value="pending">Pending</option>
                    <option value="running">Running</option>
                    <option value="completed">Completed</option>
                    <option value="failed">Failed</option>
                </select>
                <button onclick="loadJobs()">Refresh</button>
            </div>
            <table id="jobsTable">
                <thead>
                    <tr>
                        <th>Status</th>
                        <th>Message</th>
                        <th>Channel</th>
                        <th>Created</th>
                        <th>Duration</th>
                    </tr>
                </thead>
                <tbody id="jobsBody"></tbody>
            </table>
        </div>

        <div id="section-commits" class="section">
            <h1 style="margin-bottom: 20px;">Git Commits</h1>
            <div class="commits-list">
                <div class="commit-item" style="background: #0f0f23; font-weight: 600; font-size: 11px; text-transform: uppercase; color: #00d4ff;">
                    <span>Project</span>
                    <span>SHA</span>
                    <span>Description</span>
                    <span>Author</span>
                    <span>Date/Time</span>
                </div>
                <div id="commitsList"></div>
            </div>
        </div>

        <div id="section-sessions" class="section">
            <h1 style="margin-bottom: 20px;">Active Sessions</h1>
            <div class="sessions-list" id="sessionsList"></div>
        </div>

        <div id="section-console" class="section">
            <h1 style="margin-bottom: 20px;">Interactive Console</h1>
            <div style="background: #16213e; border-radius: 8px; padding: 15px; margin-bottom: 15px;">
                <input type="text" id="console-prompt" style="width: 100%; padding: 12px; background: #0a0a14; color: #eee; border: 1px solid #333; border-radius: 4px; font-family: monospace;" placeholder="Type your prompt and press Enter..." onkeypress="if(event.key==='Enter')startConsole(currentThreadTs, currentSessionId)">
                <div style="margin-top: 10px; display: flex; gap: 10px;">
                    <button onclick="startConsole()" style="padding: 10px 20px; background: #00d4ff; color: #1a1a2e; border: none; border-radius: 4px; cursor: pointer; font-weight: 600;">Connect</button>
                    <button onclick="disconnectConsole()" style="padding: 10px 20px; background: #ff4757; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: 600;">Disconnect</button>
                </div>
            </div>
            <div id="console-output" class="console" style="height: 500px; overflow-y: auto;"></div>
        </div>

        <div id="section-config" class="section">
            <h1 style="margin-bottom: 20px;">Agent Configuration</h1>
            <div class="skills-section">
            <div class="skills-tabs">
                <button class="skills-tab active" onclick="showSkillTab('claude-md')">CLAUDE.md</button>
                <button class="skills-tab" onclick="showSkillTab('settings')">Settings</button>
                <button class="skills-tab" onclick="showSkillTab('commands')">Commands</button>
                <button class="skills-tab" onclick="showSkillTab('teams')">Teams</button>
            </div>

            <div id="tab-claude-md" class="skills-content">
                <textarea id="claude-md-editor" class="skills-editor" rows="20"></textarea>
                <button class="skills-save" onclick="saveSkill('claude-md')">Save CLAUDE.md</button>
                <span id="claude-md-message" class="save-message"></span>
            </div>

            <div id="tab-settings" class="skills-content hidden">
                <textarea id="settings-editor" class="skills-editor" rows="15"></textarea>
                <button class="skills-save" onclick="saveSkill('settings')">Save Settings</button>
                <span id="settings-message" class="save-message"></span>
            </div>

            <div id="tab-commands" class="skills-content hidden">
                <div class="skills-list" id="commands-list"></div>
                <div id="command-editor-container" class="hidden" style="margin-top: 15px;">
                    <h4 style="color: #00d4ff; margin-bottom: 10px;" id="command-editor-title"></h4>
                    <textarea id="command-editor" class="skills-editor" rows="15"></textarea>
                    <button class="skills-save" onclick="saveSkill('command')">Save Command</button>
                    <span id="command-message" class="save-message"></span>
                </div>
            </div>

            <div id="tab-teams" class="skills-content hidden">
                <div class="skills-list" id="teams-list"></div>
                <div id="team-editor-container" class="hidden" style="margin-top: 15px;">
                    <h4 style="color: #00d4ff; margin-bottom: 10px;" id="team-editor-title"></h4>
                    <textarea id="team-editor" class="skills-editor" rows="15"></textarea>
                    <button class="skills-save" onclick="saveSkill('team')">Save Team</button>
                    <span id="team-message" class="save-message"></span>
                </div>
            </div>
            </div>
        </div>
        </div>

        <div class="refresh-info">Auto-refreshing every 10 seconds</div>
    </div>
    <script>
        let jobs = [];
        let expandedJob = null;

        // Sidebar navigation
        document.querySelectorAll('.nav-item').forEach(item => {
            item.addEventListener('click', function(e) {
                e.preventDefault();
                const section = this.getAttribute('data-section');

                // Update active nav item
                document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
                this.classList.add('active');

                // Show selected section
                document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
                document.getElementById('section-' + section).classList.add('active');
            });
        });

        // Simple console with WebSocket
        let consoleWs = null;
        let currentThreadTs = null;
        let currentSessionId = null;

        function startConsole(threadTs, sessionId) {
            const output = document.getElementById('console-output');
            const prompt = document.getElementById('console-prompt').value.trim();

            // Store current session info
            currentThreadTs = threadTs || null;
            currentSessionId = sessionId || null;

            // If no prompt provided, just store session and wait for user input
            if (!prompt) {
                addConsoleLine('Session loaded. Type a message and press Enter to continue.', '#666');
                return;
            }

            // Add user prompt to output
            const promptLine = document.createElement('div');
            promptLine.className = 'console-line';
            promptLine.style.color = '#00d4ff';
            promptLine.textContent = '> ' + prompt;
            output.appendChild(promptLine);
            output.scrollTop = output.scrollHeight;

            // Close existing connection
            if (consoleWs) {
                consoleWs.close();
            }

            // Connect to WebSocket
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            consoleWs = new WebSocket(protocol + '//' + window.location.host + '/ws/terminal');

            consoleWs.onopen = function() {
                consoleWs.send(JSON.stringify({
                    prompt: prompt,
                    thread_ts: currentThreadTs,
                    session_id: currentSessionId
                }));
                if (prompt) {
                    document.getElementById('console-prompt').value = '';
                }
            };

            consoleWs.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    const type = data.type;

                    if (type === 'user_prompt') {
                        // User prompt echo - don't display
                    } else if (type === 'system') {
                        addConsoleLine(data.content, '#666');
                    } else if (type === 'error') {
                        addConsoleLine('❌ Error: ' + data.content, '#ff4757');
                    } else if (type === 'thinking') {
                        addConsoleLine('🧠 ' + data.content, '#f0c674');
                    } else if (type === 'tool_start') {
                        addConsoleLine(data.content, '#81a2be');
                    } else if (type === 'tool_end') {
                        addConsoleLine(data.content, '#b5bd68');
                    } else if (type === 'message') {
                        addConsoleLine(data.content, '#c9d1d9');
                    } else if (type === 'delta') {
                        // Streaming text - append to last line
                        const output = document.getElementById('console-output');
                        let lastLine = output.lastElementChild;
                        if (!lastLine || lastLine.classList.contains('system')) {
                            lastLine = addConsoleLine('', '#c9d1d9');
                        }
                        lastLine.textContent += data.content;
                        output.scrollTop = output.scrollHeight;
                    } else if (type === 'result') {
                        addConsoleLine('📝 ' + data.content, '#b5bd68');
                    } else if (type === 'terminal') {
                        addConsoleLine(data.content, '#8abeb7');
                    } else {
                        // Unknown type
                        addConsoleLine(JSON.stringify(data), '#666');
                    }
                } catch (e) {
                    addConsoleLine(event.data, '#b5bd68');
                }
            };

            consoleWs.onclose = function() {
                addConsoleLine('⚠ Disconnected', '#d29922');
            };

            consoleWs.onerror = function(err) {
                addConsoleLine('✗ WebSocket Error', '#ff4757');
            };
        }

        function addConsoleLine(text, color) {
            const output = document.getElementById('console-output');
            const line = document.createElement('div');
            line.className = 'console-line';
            line.style.color = color;
            line.style.whiteSpace = 'pre-wrap';
            line.textContent = text;
            output.appendChild(line);
            output.scrollTop = output.scrollHeight;
            return line;
        }

        function disconnectConsole() {
            if (consoleWs) {
                consoleWs.close();
                consoleWs = null;
            }
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function formatDuration(startedAt, completedAt) {
            if (!startedAt || !completedAt) return '-';
            const start = new Date(startedAt);
            const end = new Date(completedAt);
            const seconds = Math.round((end - start) / 1000);
            if (seconds < 60) return seconds + 's';
            const mins = Math.floor(seconds / 60);
            const secs = seconds % 60;
            return mins + 'm ' + secs + 's';
        }

        function formatDate(dateStr) {
            if (!dateStr) return '-';
            const d = new Date(dateStr);
            return d.toLocaleString();
        }

        function truncate(str, len) {
            if (!str) return '-';
            return str.length > len ? str.substring(0, len) + '...' : str;
        }

        function renderStats() {
            const total = jobs.length;
            const pending = jobs.filter(j => j.status === 'pending').length;
            const running = jobs.filter(j => j.status === 'running').length;
            const completed = jobs.filter(j => j.status === 'completed').length;
            const failed = jobs.filter(j => j.status === 'failed').length;
            const totalCost = jobs.reduce((sum, j) => sum + (j.total_cost_usd || 0), 0);

            document.getElementById('stats').innerHTML = `
                <div class="stat-card"><div class="value">${total}</div><div class="label">Total Jobs</div></div>
                <div class="stat-card"><div class="value">${running}</div><div class="label">Running</div></div>
                <div class="stat-card"><div class="value">${completed}</div><div class="label">Completed</div></div>
                <div class="stat-card"><div class="value">${failed}</div><div class="label">Failed</div></div>
                <div class="stat-card"><div class="value">$${totalCost.toFixed(4)}</div><div class="label">Total Cost</div></div>
            `;
        }

        function renderJobs() {
            const search = document.getElementById('search').value.toLowerCase();
            const statusFilter = document.getElementById('statusFilter').value;

            let filtered = jobs.filter(j => {
                const matchSearch = !search || (j.message || '').toLowerCase().includes(search);
                const matchStatus = !statusFilter || j.status === statusFilter;
                return matchSearch && matchStatus;
            });

            const tbody = document.getElementById('jobsBody');
            tbody.innerHTML = filtered.map(job => {
                const progress = progressData[job.id] || [];
                // Live console - only for running jobs
                const consoleHtml = progress.length > 0 ? progress.map(p => {
                    const time = new Date(p.timestamp).toLocaleTimeString();
                    const msg = p.message || '';
                    // If it's terminal output (has current_text), show it differently
                    if (p.is_thinking) {
                        return `<div class="console-line thinking"><span class="console-time">${time}</span>🧠 ${msg}</div>`;
                    } else if (p.type === 'tool_start') {
                        return `<div class="console-line tool_start"><span class="console-time">${time}</span>🔧 ${msg}</div>`;
                    } else if (p.type === 'tool_end') {
                        return `<div class="console-line tool_end"><span class="console-time">${time}</span>✅ ${msg}</div>`;
                    } else if (p.message && p.message.startsWith('$ ')) {
                        return `<div class="console-line terminal"><span class="console-time">${time}</span><span class="console-prompt">$</span>${msg.substring(2)}</div>`;
                    } else {
                        return `<div class="console-line progress"><span class="console-time">${time}</span>${msg}</div>`;
                    }
                }).join('') : '';

                return `
                <tr onclick="toggleDetail('${job.id}')" style="cursor:pointer">
                    <td><span class="status ${job.status}">${job.status}</span></td>
                    <td class="message-cell" title="${(job.message || '').replace(/"/g, '&quot;')}">${truncate(job.message, 50)}</td>
                    <td>${job.channel}</td>
                    <td class="timestamp">${formatDate(job.created_at)}</td>
                    <td>${formatDuration(job.started_at, job.completed_at)}</td>
                </tr>
                ${expandedJob === job.id ? `
                <tr class="detail-row">
                    <td colspan="5">
                        <div class="detail-content">
                            <h4 style="color: #00d4ff; margin: 15px 0 10px;">Conversation</h4>
                            <div style="margin-bottom: 15px;">
                                <span style="color: #ffd700; font-weight: 600;">User:</span>
                                <pre style="margin-top: 5px;">${(job.message || '').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</pre>
                            </div>
                            ${job.result ? `
                            <div>
                                <span style="color: #00ff88; font-weight: 600;">Claude:</span>
                                <pre style="margin-top: 5px;">${job.result.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</pre>
                            </div>
                            ` : ''}
                            ${job.error ? `
                            <div style="color: #ff4757;">
                                <strong>Error:</strong>
                                <pre>${job.error.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</pre>
                            </div>
                            ` : ''}

                            ${consoleHtml ? `
                            <h4 style="color: #00d4ff; margin: 15px 0 10px;">Console (Live)</h4>
                            <div class="console">${consoleHtml}</div>
                            ` : ''}

                            <div class="metrics">
                                <div class="metric"><div class="metric-value">${job.input_tokens || 0}</div><div class="metric-label">Input Tokens</div></div>
                                <div class="metric"><div class="metric-value">${job.output_tokens || 0}</div><div class="metric-label">Output Tokens</div></div>
                                <div class="metric"><div class="metric-value">$${(job.total_cost_usd || 0).toFixed(4)}</div><div class="metric-label">Cost (USD)</div></div>
                            </div>
                        </div>
                    </td>
                </tr>
                ` : ''}
            `}).join('');
        }

        function toggleDetail(jobId) {
            if (expandedJob === jobId) {
                expandedJob = null;
            } else {
                expandedJob = jobId;
                loadProgress(jobId);
            }
            renderJobs();
        }

        const progressData = {};

        async function loadProgress(jobId) {
            try {
                const res = await fetch(`/api/jobs/${jobId}/progress`);
                progressData[jobId] = await res.json();
                renderJobs();
                // Keep polling for running jobs
                const job = jobs.find(j => j.id === jobId);
                if (job && job.status === 'running') {
                    setTimeout(() => loadProgress(jobId), 1000);
                }
            } catch (e) {
                console.error('Failed to load progress:', e);
            }
        }

        async function loadJobs() {
            try {
                const res = await fetch('/api/jobs?limit=100');
                jobs = await res.json();
                renderStats();
                renderJobs();
                // Reload progress for expanded job if it's running
                if (expandedJob) {
                    const job = jobs.find(j => j.id === expandedJob);
                    if (job && job.status === 'running') {
                        loadProgress(expandedJob);
                    }
                }
            } catch (e) {
                console.error('Failed to load jobs:', e);
            }
        }

        loadJobs();
        loadCommits();
        loadSessions();
        loadMonthlyStats();
        setInterval(loadJobs, 10000);
        setInterval(loadMonthlyStats, 60000);
        setInterval(loadCommits, 30000);
        setInterval(loadSessions, 10000);

        function viewSessionConsole(threadTs, sessionId) {
            // Switch to console section
            document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
            document.querySelector('[data-section="console"]').classList.add('active');
            document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
            document.getElementById('section-console').classList.add('active');

            // Clear output and show session info
            const output = document.getElementById('console-output');
            output.innerHTML = '';

            addConsoleLine('Session Console - Thread: ' + threadTs.substring(0, 16) + '...', '#00d4ff');
            addConsoleLine('Session ID: ' + sessionId.substring(0, 16) + '...', '#666');
            addConsoleLine('', '#666');
            addConsoleLine('Type a message and press Enter to continue this conversation.', '#666');

            // Store session info and connect
            startConsole(threadTs, sessionId);
        }

        async function loadSessions() {
            try {
                const res = await fetch('/api/sessions');
                const sessions = await res.json();

                const container = document.getElementById('sessionsList');
                if (!sessions || sessions.length === 0) {
                    container.innerHTML = '<div style="padding: 20px; color: #666; text-align: center;">No active sessions</div>';
                    return;
                }

                container.innerHTML = sessions.map(s => `
                    <div class="session-item">
                        <div class="session-info">
                            <span class="session-thread">Thread: ${s.thread_ts.substring(0, 12)}...</span>
                            <span class="session-id">${s.session_id.substring(0, 8)}...</span>
                        </div>
                        <div style="display: flex; gap: 8px;">
                            <button class="btn-abort" style="background: #00d4ff; color: #1a1a2e;" onclick="viewSessionConsole('${s.thread_ts}', '${s.session_id}')">Console</button>
                            <button class="btn-abort" onclick="abortSession('${s.thread_ts}')">Abort</button>
                        </div>
                    </div>
                `).join('');
            } catch (e) {
                console.error('Failed to load sessions:', e);
            }
        }

        async function abortSession(threadTs) {
            if (!confirm('Are you sure you want to abort this session?')) return;
            try {
                const res = await fetch('/api/sessions/' + threadTs, { method: 'DELETE' });
                if (res.ok) {
                    alert('Session aborted');
                    loadSessions();
                } else {
                    alert('Failed to abort session');
                }
            } catch (e) {
                console.error('Failed to abort session:', e);
                alert('Error aborting session');
            }
        }

        let currentSkillData = {};
        let currentCommand = null;
        let currentTeam = null;

        async function loadSkills() {
            try {
                const res = await fetch('/api/skills');
                currentSkillData = await res.json();

                // Populate editors
                document.getElementById('claude-md-editor').value = currentSkillData.claude_md || '';
                document.getElementById('settings-editor').value = currentSkillData.settings || '';

                // Populate commands list
                const commandsList = document.getElementById('commands-list');
                if (currentSkillData.commands && currentSkillData.commands.length > 0) {
                    commandsList.innerHTML = currentSkillData.commands.map(c => `
                        <div class="skill-item" onclick="editCommand('${c.name}')">
                            <span class="skill-item-name">/${c.name}</span>
                            <span class="skill-item-badge">command</span>
                        </div>
                    `).join('');
                } else {
                    commandsList.innerHTML = '<div style="color: #666; padding: 10px;">No commands found</div>';
                }

                // Populate teams list
                const teamsList = document.getElementById('teams-list');
                if (currentSkillData.teams && currentSkillData.teams.length > 0) {
                    teamsList.innerHTML = currentSkillData.teams.map(t => `
                        <div class="skill-item" onclick="editTeam('${t.name}')">
                            <span class="skill-item-name">${t.name}</span>
                            <span class="skill-item-badge">team</span>
                        </div>
                    `).join('');
                } else {
                    teamsList.innerHTML = '<div style="color: #666; padding: 10px;">No teams found</div>';
                }
            } catch (e) {
                console.error('Failed to load skills:', e);
            }
        }

        function showSkillTab(tabName) {
            document.querySelectorAll('.skills-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.skills-content').forEach(c => c.classList.add('hidden'));

            event.target.classList.add('active');
            document.getElementById('tab-' + tabName).classList.remove('hidden');

            if (tabName !== 'claude-md' && tabName !== 'settings') {
                // Hide editor containers when switching to list tabs
                document.getElementById('command-editor-container').classList.add('hidden');
                document.getElementById('team-editor-container').classList.add('hidden');
            }
        }

        function editCommand(name) {
            const cmd = currentSkillData.commands.find(c => c.name === name);
            if (cmd) {
                currentCommand = name;
                document.getElementById('command-editor-title').textContent = 'Command: /' + name;
                document.getElementById('command-editor').value = cmd.content;
                document.getElementById('command-editor-container').classList.remove('hidden');
            }
        }

        function editTeam(name) {
            const team = currentSkillData.teams.find(t => t.name === name);
            if (team) {
                currentTeam = name;
                document.getElementById('team-editor-title').textContent = 'Team: ' + name;
                document.getElementById('team-editor').value = team.content;
                document.getElementById('team-editor-container').classList.remove('hidden');
            }
        }

        async function saveSkill(type) {
            let url, content, messageEl;

            if (type === 'claude-md') {
                url = '/api/skills/claude-md';
                content = document.getElementById('claude-md-editor').value;
                messageEl = document.getElementById('claude-md-message');
            } else if (type === 'settings') {
                url = '/api/skills/settings';
                content = document.getElementById('settings-editor').value;
                messageEl = document.getElementById('settings-message');
            } else if (type === 'command') {
                url = '/api/skills/command/' + currentCommand;
                content = document.getElementById('command-editor').value;
                messageEl = document.getElementById('command-message');
            } else if (type === 'team') {
                url = '/api/skills/team/' + currentTeam;
                content = document.getElementById('team-editor').value;
                messageEl = document.getElementById('team-message');
            }

            try {
                const res = await fetch(url, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content })
                });

                if (res.ok) {
                    messageEl.textContent = 'Saved!';
                    setTimeout(() => messageEl.textContent = '', 2000);
                } else {
                    const err = await res.json();
                    messageEl.textContent = 'Error: ' + err.detail;
                    messageEl.style.color = '#ff4757';
                }
            } catch (e) {
                messageEl.textContent = 'Error: ' + e.message;
                messageEl.style.color = '#ff4757';
            }
        }

        loadSkills();

        let requestsChart, costsChart, tokensChart;

        async function loadCommits() {
            try {
                const res = await fetch('/api/commits?limit=20');
                const commits = await res.json();

                const container = document.getElementById('commitsList');
                if (!commits || commits.length === 0) {
                    container.innerHTML = '<div style="padding: 20px; color: #666; text-align: center;">No commits found</div>';
                    return;
                }

                container.innerHTML = commits.map(c => `
                    <div class="commit-item">
                        <span class="commit-project">${(c.project || 'Unknown').replace(/</g, '&lt;')}</span>
                        ${c.link
                            ? `<a href="${c.link}" target="_blank" class="commit-hash">${c.short_hash}</a>`
                            : `<span class="commit-hash">${c.short_hash}</span>`
                        }
                        <span class="commit-message" title="${(c.message || '').replace(/"/g, '&quot;')}">${(c.message || '').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</span>
                        <span class="commit-meta">
                            <span class="commit-author">${(c.author || '').replace(/</g, '&lt;')}</span>
                            <span class="commit-date">${new Date(c.timestamp * 1000).toLocaleString()}</span>
                        </span>
                    </div>
                `).join('');
            } catch (e) {
                console.error('Failed to load commits:', e);
            }
        }

        async function loadMonthlyStats() {
            try {
                const res = await fetch('/api/stats/monthly?months=12');
                const stats = await res.json();

                const labels = stats.map(s => s.month).reverse();
                const requests = stats.map(s => s.total_requests).reverse();
                const costs = stats.map(s => parseFloat(s.total_cost || 0)).reverse();
                const inputTokens = stats.map(s => s.input_tokens || 0).reverse();
                const outputTokens = stats.map(s => s.output_tokens || 0).reverse();

                // Requests Chart
                const ctx1 = document.getElementById('requestsChart').getContext('2d');
                if (requestsChart) requestsChart.destroy();
                requestsChart = new Chart(ctx1, {
                    type: 'bar',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Requests',
                            data: requests,
                            backgroundColor: '#00d4ff',
                            borderRadius: 4
                        }]
                    },
                    options: {
                        responsive: true,
                        plugins: { legend: { display: false } },
                        scales: {
                            x: { ticks: { color: '#888' }, grid: { color: '#333' } },
                            y: { ticks: { color: '#888' }, grid: { color: '#333' } }
                        }
                    }
                });

                // Costs Chart
                const ctx2 = document.getElementById('costsChart').getContext('2d');
                if (costsChart) costsChart.destroy();
                costsChart = new Chart(ctx2, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Cost (USD)',
                            data: costs,
                            borderColor: '#00ff88',
                            backgroundColor: 'rgba(0,255,136,0.1)',
                            fill: true,
                            tension: 0.4
                        }]
                    },
                    options: {
                        responsive: true,
                        plugins: { legend: { display: false } },
                        scales: {
                            x: { ticks: { color: '#888' }, grid: { color: '#333' } },
                            y: { ticks: { color: '#888' }, grid: { color: '#333' } }
                        }
                    }
                });

                // Tokens Chart
                const ctx3 = document.getElementById('tokensChart').getContext('2d');
                if (tokensChart) tokensChart.destroy();
                tokensChart = new Chart(ctx3, {
                    type: 'bar',
                    data: {
                        labels: labels,
                        datasets: [
                            { label: 'Input', data: inputTokens, backgroundColor: '#ffd700', stack: 'stack' },
                            { label: 'Output', data: outputTokens, backgroundColor: '#ff6b6b', stack: 'stack' }
                        ]
                    },
                    options: {
                        responsive: true,
                        plugins: { legend: { labels: { color: '#aaa' } } },
                        scales: {
                            x: { ticks: { color: '#888' }, grid: { color: '#333' } },
                            y: { ticks: { color: '#888' }, grid: { color: '#333' } }
                        }
                    }
                });
            } catch (e) {
                console.error('Failed to load monthly stats:', e);
            }
        }
    </script>
</body>
</html>"""


class InvokeRequest(BaseModel):
    """Request body for the /api/invoke endpoint."""

    channel: str
    message: str


class InvokeResponse(BaseModel):
    """Response body for the /api/invoke endpoint."""

    thread_ts: str
    session_id: str
    response: str


def create_api(
    fastapi_app: FastAPI,
    slack_client: AsyncWebClient,
    settings: Settings,
    sessions: SessionManager,
    job_tracker: JobTracker | None = None,
) -> None:
    """Register API routes on the FastAPI app."""

    async def verify_api_key(
        credentials: HTTPAuthorizationCredentials = Security(security),
    ) -> None:
        """Verify the Bearer token matches the configured API key."""
        if not settings.bender_api_key:
            raise HTTPException(
                status_code=503,
                detail="API key not configured on the server",
            )
        if credentials.credentials != settings.bender_api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

    @fastapi_app.get("/health")
    async def health_check() -> dict:
        """Health check endpoint."""
        return {"status": "ok"}

    @fastapi_app.post(
        "/api/invoke",
        response_model=InvokeResponse,
        dependencies=[Depends(verify_api_key)],
    )
    async def invoke(request: InvokeRequest) -> InvokeResponse:
        """Invoke Claude Code from an external trigger.

        Posts a message in the specified channel, creates a thread,
        invokes Claude Code, and posts the response in the thread.
        """
        logger.info("API invoke: channel=%s", request.channel)

        # Post the initial message to create a thread
        try:
            post_result = await slack_client.chat_postMessage(
                channel=request.channel,
                text=f"External trigger: {request.message}",
            )
        except SlackApiError as exc:
            logger.error("Failed to post to Slack: %s", exc)
            raise HTTPException(
                status_code=502, detail="Failed to post message to Slack"
            ) from exc

        thread_ts = post_result["ts"]
        session_id = await sessions.create_session(thread_ts)

        # Create job tracking record
        job_id = None
        if job_tracker:
            job_id = await job_tracker.create_job(
                thread_ts=thread_ts,
                channel=request.channel,
                message=request.message,
                session_id=session_id,
            )
            await job_tracker.update_job(
                job_id,
                status=JobStatus.RUNNING,
                started_at=datetime.utcnow(),
            )

        # Create progress callback for streaming
        async def update_progress(progress) -> None:
            if job_tracker and job_id:
                if progress.is_thinking:
                    await job_tracker.add_progress_event(
                        job_id, "thinking", "Thinking..."
                    )
                elif progress.tool_name:
                    if progress.tool_status == "running":
                        await job_tracker.add_progress_event(
                            job_id, "tool_start",
                            f"Running: {progress.tool_name}",
                            tool_name=progress.tool_name
                        )
                    else:
                        await job_tracker.add_progress_event(
                            job_id, "tool_end",
                            f"Completed: {progress.tool_name}",
                            tool_name=progress.tool_name
                        )

        # Invoke Claude Code with streaming
        try:
            response = await invoke_claude_streaming(
                prompt=request.message,
                workspace=settings.bender_workspace,
                session_id=session_id,
                model=settings.anthropic_model,
                timeout=settings.claude_timeout,
                progress_callback=update_progress,
                update_interval=3.0,
            )
        except ClaudeCodeError as exc:
            logger.error("Claude Code invocation failed: %s", exc)
            await slack_client.chat_postMessage(
                channel=request.channel,
                thread_ts=thread_ts,
                text="An error occurred while processing this request.",
            )
            # Update job as failed
            if job_tracker and job_id:
                await job_tracker.update_job(
                    job_id,
                    status=JobStatus.FAILED,
                    completed_at=datetime.utcnow(),
                    error=str(exc),
                )
            raise HTTPException(
                status_code=500, detail="Claude Code invocation failed"
            ) from exc

        # Post the response in the thread, handling long messages
        formatted = md_to_mrkdwn(response.result)

        # For very long responses, upload as a file
        if len(formatted) > LONG_RESPONSE_THRESHOLD:
            logger.info("Response too long (%d chars), uploading as file", len(formatted))
            try:
                file_path = create_temp_file(response.result, "claude-response")
                await slack_client.files_upload_v2(
                    channel=request.channel,
                    thread_ts=thread_ts,
                    file=str(file_path),
                    initial_comment="Response too long, here it is as a file:"
                )
            except Exception as e:
                logger.error("Failed to upload file: %s", e)
                formatted = formatted[:LONG_RESPONSE_THRESHOLD] + "\n\n[Response truncated. Full response uploaded as file failed.]"

        # Split and post the message
        chunks = split_text(formatted, SLACK_MSG_LIMIT)
        for chunk in chunks:
            await slack_client.chat_postMessage(
                channel=request.channel,
                thread_ts=thread_ts,
                text=chunk,
            )

        # Update job as completed with cost
        if job_tracker and job_id:
            await job_tracker.update_job(
                job_id,
                status=JobStatus.COMPLETED,
                completed_at=datetime.utcnow(),
                result=response.result[:5000],  # Limit result size
                total_cost_usd=getattr(response, 'total_cost', 0) or 0,
            )
            # Scan for new git commits
            try:
                await job_tracker.scan_new_commits(
                    settings.bender_workspace,
                    job_id,
                    datetime.utcnow(),
                )
            except Exception as e:
                logger.debug("Failed to scan commits: %s", e)

        return InvokeResponse(
            thread_ts=thread_ts,
            session_id=response.session_id,
            response=response.result,
        )

    @fastapi_app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard() -> str:
        """Serve the job monitoring dashboard."""
        return DASHBOARD_HTML

    @fastapi_app.get("/api/jobs")
    async def list_jobs(
        status: str | None = Query(None, description="Filter by status"),
        limit: int = Query(100, ge=1, le=500, description="Maximum jobs to return"),
        offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    ) -> list[dict]:
        """List all jobs, optionally filtered by status."""
        if not job_tracker:
            return []

        jobs = await job_tracker.get_all_jobs(status=status, limit=limit, offset=offset)
        return jobs

    @fastapi_app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict:
        """Get a specific job by ID."""
        if not job_tracker:
            raise HTTPException(status_code=404, detail="Job tracker not available")

        job = await job_tracker.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        return job

    @fastapi_app.get("/api/jobs/{job_id}/progress")
    async def get_job_progress(job_id: str) -> list[dict]:
        """Get progress events for a job."""
        if not job_tracker:
            return []

        # Verify job exists
        job = await job_tracker.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        return await job_tracker.get_progress(job_id)

    @fastapi_app.get("/api/stats/monthly")
    async def get_monthly_stats(months: int = Query(12, ge=1, le=24)) -> list[dict]:
        """Get monthly statistics for jobs."""
        if not job_tracker:
            return []

        return await job_tracker.get_monthly_stats(months)

    @fastapi_app.get("/api/commits")
    async def get_commits(limit: int = Query(50, ge=1, le=100)) -> list[dict]:
        """Get recent commits from the workspace."""
        if not job_tracker:
            return []

        return await job_tracker.get_commits_by_workspace(settings.bender_workspace, limit)

    @fastapi_app.get("/api/sessions")
    async def list_sessions() -> list[dict]:
        """List all active sessions."""
        return await sessions.list_sessions()

    @fastapi_app.delete("/api/sessions/{thread_ts}")
    async def abort_session(thread_ts: str) -> dict:
        """Abort a session by thread timestamp."""
        success = await sessions.abort_session(thread_ts)
        if success:
            return {"success": True, "message": f"Session {thread_ts} aborted"}
        else:
            raise HTTPException(status_code=404, detail="Session not found")

    @fastapi_app.get("/api/skills")
    async def get_skills() -> dict:
        """Get all skills and agent configuration from the workspace."""
        workspace = settings.bender_workspace

        result = {
            "claude_md": None,
            "settings": None,
            "commands": [],
            "teams": [],
        }

        # Read CLAUDE.md
        claude_md_path = workspace / "CLAUDE.md"
        if claude_md_path.exists():
            try:
                result["claude_md"] = claude_md_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("Failed to read CLAUDE.md: %s", e)

        # Read settings.json
        settings_path = workspace / ".claude" / "settings.json"
        if settings_path.exists():
            try:
                result["settings"] = settings_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("Failed to read settings.json: %s", e)

        # Read commands (skills)
        commands_dir = workspace / ".claude" / "commands"
        if commands_dir.exists() and commands_dir.is_dir():
            try:
                for cmd_file in commands_dir.glob("*.md"):
                    content = cmd_file.read_text(encoding="utf-8")
                    result["commands"].append({
                        "name": cmd_file.stem,
                        "file": str(cmd_file.relative_to(workspace)),
                        "content": content,
                    })
            except Exception as e:
                logger.warning("Failed to read commands: %s", e)

        # Read teams
        teams_dir = workspace / ".claude" / "teams"
        if teams_dir.exists() and teams_dir.is_dir():
            try:
                for team_dir in teams_dir.iterdir():
                    if team_dir.is_dir():
                        config_file = team_dir / "config.json"
                        if config_file.exists():
                            content = config_file.read_text(encoding="utf-8")
                            result["teams"].append({
                                "name": team_dir.name,
                                "file": str(config_file.relative_to(workspace)),
                                "content": content,
                            })
            except Exception as e:
                logger.warning("Failed to read teams: %s", e)

        return result

    @fastapi_app.put("/api/skills/claude-md")
    async def update_claude_md(content: str) -> dict:
        """Update CLAUDE.md file."""
        workspace = settings.bender_workspace
        claude_md_path = workspace / "CLAUDE.md"

        try:
            claude_md_path.write_text(content, encoding="utf-8")
            return {"success": True, "message": "CLAUDE.md updated successfully"}
        except Exception as e:
            logger.error("Failed to update CLAUDE.md: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to update: {str(e)}")

    @fastapi_app.put("/api/skills/settings")
    async def update_settings(content: str) -> dict:
        """Update settings.json file."""
        workspace = settings.bender_workspace
        settings_path = workspace / ".claude" / "settings.json"

        # Validate JSON
        import json
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(content, encoding="utf-8")
            return {"success": True, "message": "settings.json updated successfully"}
        except Exception as e:
            logger.error("Failed to update settings.json: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to update: {str(e)}")

    @fastapi_app.put("/api/skills/command/{command_name}")
    async def update_command(command_name: str, content: str) -> dict:
        """Update a command/skill file."""
        workspace = settings.bender_workspace
        command_path = workspace / ".claude" / "commands" / f"{command_name}.md"

        try:
            command_path.parent.mkdir(parents=True, exist_ok=True)
            command_path.write_text(content, encoding="utf-8")
            return {"success": True, "message": f"Command {command_name} updated successfully"}
        except Exception as e:
            logger.error("Failed to update command %s: %s", command_name, e)
            raise HTTPException(status_code=500, detail=f"Failed to update: {str(e)}")

    @fastapi_app.put("/api/skills/team/{team_name}")
    async def update_team(team_name: str, content: str) -> dict:
        """Update a team config file."""
        workspace = settings.bender_workspace

        # Validate JSON
        import json
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

        team_path = workspace / ".claude" / "teams" / team_name / "config.json"

        try:
            team_path.parent.mkdir(parents=True, exist_ok=True)
            team_path.write_text(content, encoding="utf-8")
            return {"success": True, "message": f"Team {team_name} updated successfully"}
        except Exception as e:
            logger.error("Failed to update team %s: %s", team_name, e)
            raise HTTPException(status_code=500, detail=f"Failed to update: {str(e)}")

    @fastapi_app.post("/api/stream-invoke")
    async def stream_invoke(request: InvokeRequest) -> Response:
        """Stream Claude Code output in real-time via SSE."""
        import asyncio
        import json

        async def generate():
            # Find claude executable
            claude_executable = _find_claude_executable()

            # Build command
            cmd = [
                claude_executable,
                "--print",
                "--verbose",
                "--output-format", "stream-json",
            ]

            # Add model if specified
            if settings.anthropic_model:
                cmd.extend(["--model", settings.anthropic_model])

            # Generate a session ID for this invocation
            import uuid
            session_id = str(uuid.uuid4())

            # Check if resuming or new session
            thread_ts = request.thread_ts or str(uuid.uuid4())
            existing_session = await sessions.get_session(thread_ts)

            if existing_session:
                cmd.extend(["--resume", existing_session])
            else:
                await sessions.create_session(thread_ts)
                cmd.extend(["--session-id", session_id])

            cmd.extend(["--", request.message])

            logger.info("Streaming invoke: thread=%s, session=%s", thread_ts, session_id)

            # Start process
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=settings.bender_workspace,
            )

            # Stream stdout
            try:
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    line_str = line.decode().strip()
                    if line_str:
                        yield f"data: {line_str}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            finally:
                # Also read any remaining stderr
                try:
                    stderr_data = await process.stderr.read()
                    if stderr_data:
                        stderr_str = stderr_data.decode().strip()
                        if stderr_str:
                            yield f"data: {json.dumps({'type': 'stderr', 'message': stderr_str})}\n\n"
                except Exception:
                    pass

            # Wait for process to finish
            await process.wait()

            yield f"data: {json.dumps({'type': 'done', 'returncode': process.returncode})}\n\n"

        # Return streaming response
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @fastapi_app.websocket("/ws/terminal")
    async def websocket_terminal(websocket: WebSocket):
        """Interactive terminal via WebSocket with PTY-like streaming."""
        await websocket.accept()
        logger.info("WebSocket terminal connected")

        # Store current session info
        current_session_id = None
        current_thread_ts = None

        async def run_claude(prompt: str):
            """Run Claude and stream output."""
            nonlocal current_session_id

            # Generate session ID if needed
            if not current_session_id:
                import uuid
                current_session_id = str(uuid.uuid4())

            # Build command
            claude_executable = _find_claude_executable()
            cmd = [
                claude_executable,
                "--print",
                "--verbose",
                "--output-format", "stream-json",
            ]

            if settings.anthropic_model:
                cmd.extend(["--model", settings.anthropic_model])

            # Handle session resume
            if current_thread_ts:
                existing_session = await sessions.get_session(current_thread_ts)
                if existing_session:
                    cmd.extend(["--resume", existing_session])
                    current_session_id = existing_session
                else:
                    await sessions.create_session(current_thread_ts)
                    cmd.extend(["--session-id", current_session_id])
            else:
                cmd.extend(["--session-id", current_session_id])

            cmd.extend(["--", prompt])

            logger.info("Starting Claude: %s", " ".join(cmd[-3:]))

            # Start process
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=settings.bender_workspace,
            )

            # Stream output
            async def stream_output(stream, stream_name):
                try:
                    while True:
                        line = await stream.readline()
                        if not line:
                            break
                        line_str = line.decode().strip()
                        if not line_str:
                            continue

                        try:
                            parsed = json.loads(line_str)
                            event_type = parsed.get("type", "")
                            subtype = parsed.get("subtype", "")

                            # Skip init
                            if event_type == "system" or subtype == "init":
                                continue

                            # Thinking
                            if event_type == "thinking":
                                await websocket.send_text(json.dumps({
                                    "type": "thinking",
                                    "content": parsed.get("thinking", "")
                                }))
                            # Tool start
                            elif event_type == "tool_use":
                                tool_name = parsed.get("tool", {}).get("name", "unknown")
                                await websocket.send_text(json.dumps({
                                    "type": "tool_start",
                                    "content": f"🔧 Running: {tool_name}"
                                }))
                            # Tool end
                            elif event_type == "tool_result":
                                await websocket.send_text(json.dumps({
                                    "type": "tool_end",
                                    "content": "✅ Done"
                                }))
                            # Message
                            elif event_type == "message":
                                msg = parsed.get("message", {})
                                content = msg.get("content", [])
                                text_content = ""
                                for block in content:
                                    if block.get("type") == "text":
                                        text_content += block.get("text", "")
                                if text_content:
                                    await websocket.send_text(json.dumps({
                                        "type": "message",
                                        "content": text_content
                                    }))
                            # Content delta (streaming)
                            elif event_type == "content_block_delta":
                                delta = parsed.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    await websocket.send_text(json.dumps({
                                        "type": "delta",
                                        "content": delta.get("text", "")
                                    }))
                            # Result
                            elif event_type == "result":
                                result = parsed.get("result", "")
                                if result:
                                    await websocket.send_text(json.dumps({
                                        "type": "result",
                                        "content": result
                                    }))
                            # Error
                            elif event_type == "error":
                                await websocket.send_text(json.dumps({
                                    "type": "error",
                                    "content": parsed.get("error", {}).get("message", "Unknown error")
                                }))
                        except json.JSONDecodeError:
                            await websocket.send_text(json.dumps({
                                "type": "terminal",
                                "content": line_str
                            }))
                except Exception as e:
                    logger.warning(f"Error streaming: {e}")

            stdout_task = asyncio.create_task(stream_output(process.stdout, "stdout"))
            stderr_task = asyncio.create_task(stream_output(process.stderr, "stderr"))

            returncode = await process.wait()
            await stdout_task
            await stderr_task

            return returncode

        try:
            # Main loop - wait for messages
            while True:
                try:
                    data = await asyncio.wait_for(websocket.receive_text(), timeout=300)
                except asyncio.TimeoutError:
                    # No message for 5 minutes, close connection
                    await websocket.send_text(json.dumps({
                        "type": "system",
                        "content": "\n⏰ Connection timed out (5 min inactivity)"
                    }))
                    break

                message_data = {}
                try:
                    message_data = json.loads(data)
                except json.JSONDecodeError:
                    message_data = {"prompt": data}

                prompt = message_data.get("prompt", data)
                thread_ts = message_data.get("thread_ts")
                session_id = message_data.get("session_id")

                # Update session info
                if thread_ts:
                    current_thread_ts = thread_ts
                if session_id:
                    current_session_id = session_id

                if not prompt:
                    await websocket.send_text(json.dumps({
                        "type": "system",
                        "content": "⚠️ Please enter a prompt"
                    }))
                    continue

                # Show user prompt
                await websocket.send_text(json.dumps({
                    "type": "user_prompt",
                    "content": f"> {prompt}"
                }))

                # Run Claude
                returncode = await run_claude(prompt)

                # Show completion
                await websocket.send_text(json.dumps({
                    "type": "system",
                    "content": f"\n[Completed - exit code: {returncode}]\n\n💬 Send another message to continue..."
                }))

        except WebSocketDisconnect:
            logger.info("WebSocket terminal disconnected")
        except Exception as e:
            logger.error(f"WebSocket terminal error: {e}")
            try:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "content": str(e)
                }))
            except Exception:
                pass
        finally:
            try:
                await websocket.close()
            except Exception:
                pass


def _find_claude_executable() -> str:
    """Find Claude Code executable."""
    import shutil
    import os

    # First try shutil.which (checks PATH)
    claude_path = shutil.which("claude")
    if claude_path:
        return claude_path

    # On Windows, try common npm global installation paths
    if os.name == "nt":
        common_paths = [
            os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd"),
            os.path.join(os.environ.get("ProgramFiles", ""), "nodejs", "claude.cmd"),
        ]
        for path in common_paths:
            if os.path.isfile(path):
                return path
    else:
        home = os.path.expanduser("~")
        common_paths = [
            "/usr/local/bin/claude",
            os.path.join(home, ".npm-global", "bin", "claude"),
            os.path.join(home, ".local", "bin", "claude"),
        ]
        for path in common_paths:
            if os.path.isfile(path):
                return path

    raise ClaudeCodeError(
        "Claude Code CLI not found. Ensure 'claude' is installed and in PATH."
    )
