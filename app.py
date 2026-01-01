#!/usr/bin/env python3
"""
FastAPI app for exploring Nevada medical malpractice complaints.

Usage:
    uv run uvicorn app:app --reload
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pymongo import MongoClient

load_dotenv()

app = FastAPI(title="Nevada Medical Malpractice Explorer")

# Serve PDFs from pdfs_ocr folder
PDFS_DIR = Path(__file__).parent / "pdfs_ocr"
if PDFS_DIR.exists():
    app.mount("/pdfs", StaticFiles(directory=str(PDFS_DIR)), name="pdfs")

# MongoDB connection
mongo_client: MongoClient = None
db = None
complaints = None


@app.on_event("startup")
def startup():
    global mongo_client, db, complaints
    mongo_uri = os.environ.get("MONGODB_URI")
    if not mongo_uri:
        raise ValueError("MONGODB_URI environment variable is required")
    mongo_client = MongoClient(mongo_uri)
    db = mongo_client["malpractice"]
    complaints = db["complaints"]


@app.on_event("shutdown")
def shutdown():
    if mongo_client:
        mongo_client.close()


@app.get("/", response_class=HTMLResponse)
def home():
    """Serve the main explorer UI."""
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nevada Medical Malpractice Explorer</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }
        h1 { color: #333; margin-bottom: 5px; }
        .subtitle { color: #666; margin-bottom: 20px; }
        .filters {
            background: white;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .filter-row {
            display: flex;
            flex-wrap: wrap;
            gap: 15px;
            margin-bottom: 15px;
        }
        .filter-group {
            display: flex;
            flex-direction: column;
            min-width: 150px;
        }
        .filter-group label {
            font-size: 12px;
            font-weight: 600;
            color: #555;
            margin-bottom: 4px;
            text-transform: uppercase;
        }
        select, input {
            padding: 8px 12px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
        }
        button {
            padding: 10px 20px;
            background: #2563eb;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
        }
        button:hover { background: #1d4ed8; }
        button.secondary {
            background: #6b7280;
        }
        button.secondary:hover { background: #4b5563; }
        .stats {
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }
        .stat {
            background: white;
            padding: 15px 20px;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .stat-value { font-size: 24px; font-weight: bold; color: #2563eb; }
        .stat-label { font-size: 12px; color: #666; text-transform: uppercase; }
        .results {
            background: white;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            overflow: hidden;
        }
        .complaint {
            padding: 20px;
            border-bottom: 1px solid #eee;
        }
        .complaint:last-child { border-bottom: none; }
        .complaint-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 10px;
        }
        .respondent { font-weight: 600; font-size: 16px; color: #333; }
        .case-number { font-size: 12px; color: #888; }
        .category {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 500;
            background: #dbeafe;
            color: #1e40af;
        }
        .category.controlled { background: #fef3c7; color: #92400e; }
        .category.license { background: #fce7f3; color: #9d174d; }
        .category.sexual { background: #fee2e2; color: #991b1b; }
        .category.impairment { background: #e0e7ff; color: #3730a3; }
        .summary { color: #444; margin: 10px 0; line-height: 1.5; }
        .meta {
            display: flex;
            flex-wrap: wrap;
            gap: 15px;
            font-size: 13px;
            color: #666;
        }
        .meta-item { display: flex; align-items: center; gap: 4px; }
        .drugs {
            display: flex;
            flex-wrap: wrap;
            gap: 5px;
            margin-top: 10px;
        }
        .drug {
            background: #f3f4f6;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 12px;
            color: #374151;
        }
        .complainants {
            margin-top: 10px;
            font-size: 13px;
            color: #666;
        }
        .no-results {
            padding: 40px;
            text-align: center;
            color: #666;
        }
        .loading {
            padding: 40px;
            text-align: center;
            color: #666;
        }
        .pagination {
            display: flex;
            justify-content: center;
            gap: 10px;
            padding: 20px;
        }
        .complaint { cursor: pointer; transition: background 0.2s; }
        .complaint:hover { background: #f9fafb; }

        /* Modal styles */
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            z-index: 1000;
            overflow: auto;
        }
        .modal-overlay.active { display: flex; }
        .modal {
            background: white;
            margin: 20px auto;
            width: 95%;
            max-width: 1400px;
            border-radius: 12px;
            overflow: hidden;
            max-height: calc(100vh - 40px);
            display: flex;
            flex-direction: column;
        }
        .modal-header {
            padding: 20px;
            border-bottom: 1px solid #eee;
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            flex-shrink: 0;
        }
        .modal-close {
            background: none;
            border: none;
            font-size: 28px;
            cursor: pointer;
            color: #666;
            padding: 0;
            line-height: 1;
        }
        .modal-close:hover { color: #333; }
        .modal-body {
            display: flex;
            flex: 1;
            overflow: hidden;
        }
        .modal-info {
            width: 400px;
            padding: 20px;
            overflow-y: auto;
            border-right: 1px solid #eee;
            flex-shrink: 0;
        }
        .modal-pdf {
            flex: 1;
            background: #333;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .modal-pdf iframe {
            width: 100%;
            height: 100%;
            border: none;
        }
        .modal-pdf .no-pdf {
            color: #999;
            text-align: center;
            padding: 40px;
        }
        .info-section {
            margin-bottom: 20px;
        }
        .info-section h3 {
            font-size: 12px;
            text-transform: uppercase;
            color: #666;
            margin-bottom: 8px;
        }
        .info-section p {
            margin: 0;
            color: #333;
        }
        .info-list {
            list-style: none;
            padding: 0;
            margin: 0;
        }
        .info-list li {
            padding: 4px 0;
            color: #333;
        }
        .tabs {
            display: flex;
            border-bottom: 1px solid #ddd;
            margin-bottom: 15px;
        }
        .tab {
            padding: 8px 16px;
            cursor: pointer;
            border: none;
            background: none;
            font-size: 14px;
            color: #666;
            border-bottom: 2px solid transparent;
            margin-bottom: -1px;
        }
        .tab:hover { color: #333; }
        .tab.active {
            color: #2563eb;
            border-bottom-color: #2563eb;
            font-weight: 500;
        }
        .settlement-section {
            background: #f0fdf4;
            border: 1px solid #bbf7d0;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 15px;
        }
        .settlement-section h3 {
            color: #166534;
            margin-top: 0;
        }
        .penalty-item {
            display: flex;
            justify-content: space-between;
            padding: 6px 0;
            border-bottom: 1px solid #dcfce7;
        }
        .penalty-item:last-child { border-bottom: none; }
        .penalty-label { color: #555; }
        .penalty-value { font-weight: 600; color: #166534; }
        .violation-item {
            background: #fef2f2;
            border: 1px solid #fecaca;
            border-radius: 4px;
            padding: 8px 12px;
            margin-bottom: 8px;
            font-size: 13px;
        }
        .violation-item.dismissed {
            background: #f0f9ff;
            border-color: #bae6fd;
        }
        .violation-code { font-weight: 600; color: #991b1b; }
        .violation-item.dismissed .violation-code { color: #0369a1; }
        .no-settlement {
            color: #666;
            font-style: italic;
            padding: 20px;
            text-align: center;
        }
        /* Main navigation tabs */
        .main-tabs {
            display: flex;
            gap: 0;
            margin-bottom: 20px;
            background: white;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .main-tab {
            flex: 1;
            padding: 15px 30px;
            border: none;
            background: white;
            font-size: 16px;
            font-weight: 500;
            color: #666;
            cursor: pointer;
            border-bottom: 3px solid transparent;
            transition: all 0.2s;
        }
        .main-tab:hover { background: #f9fafb; }
        .main-tab.active {
            color: #2563eb;
            border-bottom-color: #2563eb;
            background: #eff6ff;
        }
        .view { display: none; }
        .view.active { display: block; }
        /* Statistics page styles */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .chart-card {
            background: white;
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .chart-card h3 {
            margin: 0 0 15px 0;
            color: #333;
            font-size: 16px;
        }
        .chart-container {
            position: relative;
            height: 300px;
        }
        .chart-container.tall { height: 400px; }
        .summary-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }
        .summary-card {
            background: white;
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            text-align: center;
        }
        .summary-card .value {
            font-size: 28px;
            font-weight: bold;
            color: #2563eb;
        }
        .summary-card .label {
            font-size: 12px;
            color: #666;
            text-transform: uppercase;
            margin-top: 5px;
        }
        .totals-section {
            margin-bottom: 25px;
        }
        .totals-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }
        .total-card {
            background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
            border-radius: 12px;
            padding: 25px;
            color: white;
            box-shadow: 0 4px 6px rgba(37, 99, 235, 0.3);
        }
        .total-card.green {
            background: linear-gradient(135deg, #10b981 0%, #059669 100%);
            box-shadow: 0 4px 6px rgba(16, 185, 129, 0.3);
        }
        .total-card.amber {
            background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
            box-shadow: 0 4px 6px rgba(245, 158, 11, 0.3);
        }
        .total-card.purple {
            background: linear-gradient(135deg, #8b5cf6 0%, #7c3aed 100%);
            box-shadow: 0 4px 6px rgba(139, 92, 246, 0.3);
        }
        .total-card.cyan {
            background: linear-gradient(135deg, #06b6d4 0%, #0891b2 100%);
            box-shadow: 0 4px 6px rgba(6, 182, 212, 0.3);
        }
        .total-card .value {
            font-size: 32px;
            font-weight: bold;
            margin-bottom: 5px;
        }
        .total-card .label {
            font-size: 13px;
            opacity: 0.9;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .total-card .sublabel {
            font-size: 12px;
            opacity: 0.7;
            margin-top: 8px;
        }
        .histogram-info {
            font-size: 13px;
            color: #666;
            margin-top: 10px;
            display: flex;
            justify-content: space-between;
        }
    </style>
</head>
<body>
    <h1>Nevada Medical Malpractice Explorer</h1>
    <p class="subtitle">Browse complaints from the Nevada State Board of Medical Examiners</p>

    <div class="main-tabs">
        <button class="main-tab active" onclick="switchView('cases')">Cases</button>
        <button class="main-tab" onclick="switchView('statistics')">Statistics</button>
    </div>

    <!-- Cases View -->
    <div id="cases-view" class="view active">
        <div class="stats" id="stats"></div>

        <div class="filters">
        <div class="filter-row">
            <div class="filter-group">
                <label>Category</label>
                <select id="category">
                    <option value="">All Categories</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Specialty</label>
                <select id="specialty">
                    <option value="">All Specialties</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Year</label>
                <select id="year">
                    <option value="">All Years</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Drug Mentioned</label>
                <select id="drug">
                    <option value="">Any Drug</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Patient Sex</label>
                <select id="sex">
                    <option value="">Any</option>
                    <option value="male">Male</option>
                    <option value="female">Female</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Sort By</label>
                <select id="sort">
                    <option value="date_desc">Date (Newest)</option>
                    <option value="date_asc">Date (Oldest)</option>
                    <option value="year_desc">Year (Newest)</option>
                    <option value="year_asc">Year (Oldest)</option>
                    <option value="respondent">Respondent Name</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Settlement</label>
                <select id="has_settlement">
                    <option value="">All</option>
                    <option value="yes">Has Settlement</option>
                    <option value="no">No Settlement</option>
                </select>
            </div>
        </div>
        <div class="filter-row">
            <button onclick="search()">Search</button>
            <button class="secondary" onclick="loadRandom()">Random Complaint</button>
            <button class="secondary" onclick="resetFilters()">Reset</button>
        </div>
    </div>

    <div class="results" id="results">
        <div class="loading">Loading...</div>
    </div>

    <div class="pagination" id="pagination"></div>
    </div>

    <!-- Statistics View -->
    <div id="statistics-view" class="view">
        <div class="totals-section">
            <div class="totals-grid" id="totals-grid"></div>
        </div>
        <div class="summary-grid" id="settlement-summary"></div>

        <div class="stats-grid">
            <div class="chart-card">
                <h3>Cases by Year</h3>
                <div class="chart-container">
                    <canvas id="yearChart"></canvas>
                </div>
            </div>
            <div class="chart-card">
                <h3>Cases by Category</h3>
                <div class="chart-container">
                    <canvas id="categoryChart"></canvas>
                </div>
            </div>
        </div>

        <div class="stats-grid">
            <div class="chart-card">
                <h3>Top Specialties</h3>
                <div class="chart-container tall">
                    <canvas id="specialtyChart"></canvas>
                </div>
            </div>
            <div class="chart-card">
                <h3>License Actions</h3>
                <div class="chart-container tall">
                    <canvas id="actionsChart"></canvas>
                </div>
            </div>
        </div>

        <div class="stats-grid">
            <div class="chart-card">
                <h3>Fine Amounts Distribution</h3>
                <div class="chart-container">
                    <canvas id="fineChart"></canvas>
                </div>
                <div class="histogram-info" id="fine-info"></div>
            </div>
            <div class="chart-card">
                <h3>Investigation Costs Distribution</h3>
                <div class="chart-container">
                    <canvas id="costChart"></canvas>
                </div>
                <div class="histogram-info" id="cost-info"></div>
            </div>
        </div>

        <div class="stats-grid">
            <div class="chart-card">
                <h3>CME Hours Required</h3>
                <div class="chart-container">
                    <canvas id="cmeChart"></canvas>
                </div>
                <div class="histogram-info" id="cme-info"></div>
            </div>
            <div class="chart-card">
                <h3>Probation Duration (Months)</h3>
                <div class="chart-container">
                    <canvas id="probationChart"></canvas>
                </div>
                <div class="histogram-info" id="probation-info"></div>
            </div>
        </div>
    </div>

    <!-- Modal -->
    <div class="modal-overlay" id="modal" onclick="closeModal(event)">
        <div class="modal" onclick="event.stopPropagation()">
            <div class="modal-header">
                <div>
                    <div class="respondent" id="modal-respondent"></div>
                    <div class="case-number" id="modal-case"></div>
                </div>
                <button class="modal-close" onclick="closeModal()">&times;</button>
            </div>
            <div class="modal-body">
                <div class="modal-info" id="modal-info"></div>
                <div class="modal-pdf-container" style="flex: 1; display: flex; flex-direction: column;">
                    <div class="tabs" id="pdf-tabs">
                        <button class="tab active" onclick="switchTab('complaint')">Complaint</button>
                        <button class="tab" onclick="switchTab('settlement')" id="settlement-tab" style="display:none;">Settlement</button>
                    </div>
                    <div class="modal-pdf" id="modal-pdf" style="flex: 1;"></div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let currentPage = 0;
        const pageSize = 20;
        let chartsLoaded = false;
        let charts = {};

        function switchView(view) {
            document.querySelectorAll('.main-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
            document.querySelector(`.main-tab[onclick="switchView('${view}')"]`).classList.add('active');
            document.getElementById(`${view}-view`).classList.add('active');

            if (view === 'statistics' && !chartsLoaded) {
                loadAnalytics();
            }
        }

        async function loadFilters() {
            const res = await fetch('/api/filters');
            const data = await res.json();

            // Populate category dropdown
            const categorySelect = document.getElementById('category');
            data.categories.forEach(c => {
                const opt = document.createElement('option');
                opt.value = c;
                opt.textContent = c;
                categorySelect.appendChild(opt);
            });

            // Populate specialty dropdown
            const specialtySelect = document.getElementById('specialty');
            data.specialties.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s;
                opt.textContent = s;
                specialtySelect.appendChild(opt);
            });

            // Populate year dropdown
            const yearSelect = document.getElementById('year');
            data.years.forEach(y => {
                const opt = document.createElement('option');
                opt.value = y;
                opt.textContent = y;
                yearSelect.appendChild(opt);
            });

            // Populate drug dropdown
            const drugSelect = document.getElementById('drug');
            data.drugs.slice(0, 50).forEach(d => {
                const opt = document.createElement('option');
                opt.value = d;
                opt.textContent = d;
                drugSelect.appendChild(opt);
            });
        }

        async function loadStats() {
            const res = await fetch('/api/stats');
            const data = await res.json();

            document.getElementById('stats').innerHTML = `
                <div class="stat">
                    <div class="stat-value">${data.total}</div>
                    <div class="stat-label">Total Complaints</div>
                </div>
                <div class="stat">
                    <div class="stat-value">${data.with_extraction}</div>
                    <div class="stat-label">Processed</div>
                </div>
                <div class="stat">
                    <div class="stat-value">${data.settlements}</div>
                    <div class="stat-label">Settlements</div>
                </div>
                <div class="stat">
                    <div class="stat-value">${data.categories}</div>
                    <div class="stat-label">Categories</div>
                </div>
                <div class="stat">
                    <div class="stat-value">${data.drugs}</div>
                    <div class="stat-label">Unique Drugs</div>
                </div>
            `;
        }

        function getCategoryClass(category) {
            if (category?.includes('Controlled')) return 'controlled';
            if (category?.includes('License')) return 'license';
            if (category?.includes('Sexual')) return 'sexual';
            if (category?.includes('Impairment')) return 'impairment';
            return '';
        }

        // Store complaints and settlements for modal access
        let complaintsCache = {};
        let currentSettlement = null;
        let currentComplaint = null;
        let complaintPdfPath = null;
        let settlementPdfPath = null;

        function renderComplaint(c) {
            // Cache the complaint data
            complaintsCache[c.case_number] = c;

            const ext = c.llm_extracted || {};
            const complainants = ext.complainants || [];
            const drugs = ext.drugs || [];

            let complainantText = '';
            if (complainants.length > 0) {
                const parts = complainants.map(p => {
                    const age = p.age ? `${p.age}yo` : '';
                    const sex = p.sex || '';
                    return [age, sex].filter(Boolean).join(' ') || 'Unknown';
                });
                complainantText = `Patients: ${parts.join(', ')}`;
            }

            return `
                <div class="complaint" onclick="openModal('${c.case_number}')">
                    <div class="complaint-header">
                        <div>
                            <div class="respondent">${c.respondent}</div>
                            <div class="case-number">Case ${c.case_number} • ${c.date}</div>
                        </div>
                        ${ext.category ? `<span class="category ${getCategoryClass(ext.category)}">${ext.category}</span>` : ''}
                    </div>
                    <div class="summary">${ext.summary || 'No summary available'}</div>
                    <div class="meta">
                        ${ext.specialty ? `<span class="meta-item">🩺 ${ext.specialty}</span>` : ''}
                        ${ext.procedure ? `<span class="meta-item">💉 ${ext.procedure}</span>` : ''}
                        ${ext.num_complainants ? `<span class="meta-item">👥 ${ext.num_complainants} patient(s)</span>` : ''}
                    </div>
                    ${complainantText ? `<div class="complainants">${complainantText}</div>` : ''}
                    ${drugs.length > 0 ? `
                        <div class="drugs">
                            ${drugs.map(d => `<span class="drug">${d}</span>`).join('')}
                        </div>
                    ` : ''}
                </div>
            `;
        }

        async function openModal(caseNumber) {
            const c = complaintsCache[caseNumber];
            if (!c) return;

            currentComplaint = c;
            currentSettlement = null;

            const ext = c.llm_extracted || {};
            const complainants = ext.complainants || [];
            const drugs = ext.drugs || [];

            document.getElementById('modal-respondent').textContent = c.respondent;
            document.getElementById('modal-case').textContent = `Case ${c.case_number} • ${c.date} • ${c.type}`;

            // Build complaint info panel
            let infoHtml = '';

            if (ext.category) {
                infoHtml += `
                    <div class="info-section">
                        <h3>Category</h3>
                        <p><span class="category ${getCategoryClass(ext.category)}">${ext.category}</span></p>
                    </div>
                `;
            }

            if (ext.summary) {
                infoHtml += `
                    <div class="info-section">
                        <h3>Summary</h3>
                        <p>${ext.summary}</p>
                    </div>
                `;
            }

            if (ext.specialty) {
                infoHtml += `
                    <div class="info-section">
                        <h3>Specialty</h3>
                        <p>${ext.specialty}</p>
                    </div>
                `;
            }

            if (ext.procedure) {
                infoHtml += `
                    <div class="info-section">
                        <h3>Procedure</h3>
                        <p>${ext.procedure}</p>
                    </div>
                `;
            }

            if (complainants.length > 0) {
                infoHtml += `
                    <div class="info-section">
                        <h3>Patients (${ext.num_complainants || complainants.length})</h3>
                        <ul class="info-list">
                            ${complainants.map((p, i) => {
                                const age = p.age ? `${p.age} years old` : 'Age unknown';
                                const sex = p.sex ? p.sex.charAt(0).toUpperCase() + p.sex.slice(1) : 'Sex unknown';
                                return `<li>Patient ${i + 1}: ${sex}, ${age}</li>`;
                            }).join('')}
                        </ul>
                    </div>
                `;
            }

            if (drugs.length > 0) {
                infoHtml += `
                    <div class="info-section">
                        <h3>Drugs Mentioned</h3>
                        <div class="drugs">
                            ${drugs.map(d => `<span class="drug">${d}</span>`).join('')}
                        </div>
                    </div>
                `;
            }

            // Fetch settlement data
            try {
                const res = await fetch(`/api/settlement/${caseNumber}`);
                const settlement = await res.json();
                if (settlement && settlement.case_number) {
                    currentSettlement = settlement;
                    infoHtml += renderSettlementInfo(settlement);
                    document.getElementById('settlement-tab').style.display = 'block';
                    settlementPdfPath = buildSettlementPdfPath(settlement);
                } else {
                    document.getElementById('settlement-tab').style.display = 'none';
                    settlementPdfPath = null;
                }
            } catch (e) {
                document.getElementById('settlement-tab').style.display = 'none';
                settlementPdfPath = null;
            }

            document.getElementById('modal-info').innerHTML = infoHtml;

            // Build PDF paths
            complaintPdfPath = c.pdf_path || buildPdfPath(c);

            // Reset to complaint tab
            switchTab('complaint');

            document.getElementById('modal').classList.add('active');
            document.body.style.overflow = 'hidden';
        }

        function renderSettlementInfo(s) {
            const ext = s.llm_extracted || {};
            let html = '<div class="settlement-section"><h3>⚖️ Settlement Outcome</h3>';

            // License action
            if (ext.license_action) {
                html += `<div class="penalty-item"><span class="penalty-label">License Action</span><span class="penalty-value">${ext.license_action}</span></div>`;
            }

            // Probation
            if (ext.probation_months) {
                const years = Math.floor(ext.probation_months / 12);
                const months = ext.probation_months % 12;
                const duration = years > 0 ? `${years} year${years > 1 ? 's' : ''}${months > 0 ? ` ${months} mo` : ''}` : `${months} months`;
                html += `<div class="penalty-item"><span class="penalty-label">Probation</span><span class="penalty-value">${duration}</span></div>`;
            }

            // Fine
            if (ext.fine_amount) {
                html += `<div class="penalty-item"><span class="penalty-label">Fine</span><span class="penalty-value">$${ext.fine_amount.toLocaleString()}</span></div>`;
            }

            // Investigation costs
            if (ext.investigation_costs) {
                html += `<div class="penalty-item"><span class="penalty-label">Investigation Costs</span><span class="penalty-value">$${ext.investigation_costs.toLocaleString()}</span></div>`;
            }

            // CME
            if (ext.cme_hours) {
                let cme = `${ext.cme_hours} hours`;
                if (ext.cme_topic) cme += ` (${ext.cme_topic})`;
                html += `<div class="penalty-item"><span class="penalty-label">CME Required</span><span class="penalty-value">${cme}</span></div>`;
            }

            // Reprimand & NPDB
            if (ext.public_reprimand) {
                html += `<div class="penalty-item"><span class="penalty-label">Public Reprimand</span><span class="penalty-value">Yes</span></div>`;
            }
            if (ext.npdb_report) {
                html += `<div class="penalty-item"><span class="penalty-label">Reported to NPDB</span><span class="penalty-value">Yes</span></div>`;
            }

            html += '</div>';

            // Violations admitted
            if (ext.violations_admitted && ext.violations_admitted.length > 0) {
                html += '<div class="info-section"><h3>Violations Admitted</h3>';
                ext.violations_admitted.forEach(v => {
                    html += `<div class="violation-item"><span class="violation-code">${v.nrs_code || v.count}</span><br>${v.description}</div>`;
                });
                html += '</div>';
            }

            // Violations dismissed
            if (ext.violations_dismissed && ext.violations_dismissed.length > 0) {
                html += '<div class="info-section"><h3>Violations Dismissed</h3>';
                ext.violations_dismissed.forEach(v => {
                    html += `<div class="violation-item dismissed"><span class="violation-code">${v.nrs_code || v.count}</span><br>${v.description}</div>`;
                });
                html += '</div>';
            }

            return html;
        }

        function buildSettlementPdfPath(s) {
            const typeSlug = s.type.replace(/[,]/g, '').replace(/\\s+/g, '_').substring(0, 30);
            return `/pdfs/${s.year}/${s.case_number}_${typeSlug}.pdf`;
        }

        function switchTab(tab) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelector(`.tab[onclick="switchTab('${tab}')"]`).classList.add('active');

            const pdfPath = tab === 'complaint' ? complaintPdfPath : settlementPdfPath;
            if (pdfPath) {
                document.getElementById('modal-pdf').innerHTML = `<iframe src="${pdfPath}"></iframe>`;
            } else {
                document.getElementById('modal-pdf').innerHTML = `<div class="no-pdf">PDF not available</div>`;
            }
        }

        function buildPdfPath(c) {
            // Build PDF path from case info: /pdfs/{year}/{case_number}_{type_slug}.pdf
            const typeSlug = c.type.replace(/[,]/g, '').replace(/\s+/g, '_').substring(0, 30);
            return `/pdfs/${c.year}/${c.case_number}_${typeSlug}.pdf`;
        }

        function closeModal(event) {
            if (event && event.target !== event.currentTarget) return;
            document.getElementById('modal').classList.remove('active');
            document.body.style.overflow = '';
            document.getElementById('modal-pdf').innerHTML = '';
            currentSettlement = null;
            currentComplaint = null;
            complaintPdfPath = null;
            settlementPdfPath = null;
        }

        // Close modal on escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') closeModal();
        });

        async function search(page = 0) {
            currentPage = page;
            const params = new URLSearchParams();

            const category = document.getElementById('category').value;
            const specialty = document.getElementById('specialty').value;
            const year = document.getElementById('year').value;
            const drug = document.getElementById('drug').value;
            const sex = document.getElementById('sex').value;
            const sort = document.getElementById('sort').value;
            const hasSettlement = document.getElementById('has_settlement').value;

            if (category) params.set('category', category);
            if (specialty) params.set('specialty', specialty);
            if (year) params.set('year', year);
            if (drug) params.set('drug', drug);
            if (sex) params.set('sex', sex);
            if (sort) params.set('sort', sort);
            if (hasSettlement) params.set('has_settlement', hasSettlement);
            params.set('skip', page * pageSize);
            params.set('limit', pageSize);

            document.getElementById('results').innerHTML = '<div class="loading">Loading...</div>';

            const res = await fetch('/api/complaints?' + params.toString());
            const data = await res.json();

            if (data.complaints.length === 0) {
                document.getElementById('results').innerHTML = '<div class="no-results">No complaints found matching your criteria.</div>';
                document.getElementById('pagination').innerHTML = '';
                return;
            }

            document.getElementById('results').innerHTML = data.complaints.map(renderComplaint).join('');

            // Pagination
            const totalPages = Math.ceil(data.total / pageSize);
            let paginationHtml = '';
            if (currentPage > 0) {
                paginationHtml += `<button onclick="search(${currentPage - 1})">← Previous</button>`;
            }
            paginationHtml += `<span style="padding: 10px;">Page ${currentPage + 1} of ${totalPages} (${data.total} total)</span>`;
            if (currentPage < totalPages - 1) {
                paginationHtml += `<button onclick="search(${currentPage + 1})">Next →</button>`;
            }
            document.getElementById('pagination').innerHTML = paginationHtml;
        }

        async function loadRandom() {
            document.getElementById('results').innerHTML = '<div class="loading">Loading random complaint...</div>';
            const res = await fetch('/api/random');
            const c = await res.json();
            complaintsCache[c.case_number] = c;  // Cache for modal
            document.getElementById('results').innerHTML = renderComplaint(c);
            document.getElementById('pagination').innerHTML = `
                <button class="secondary" onclick="loadRandom()">Another Random</button>
            `;
        }

        function resetFilters() {
            document.getElementById('category').value = '';
            document.getElementById('specialty').value = '';
            document.getElementById('year').value = '';
            document.getElementById('drug').value = '';
            document.getElementById('sex').value = '';
            document.getElementById('sort').value = 'date_desc';
            document.getElementById('has_settlement').value = '';
            search();
        }

        // Chart colors
        const chartColors = [
            '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
            '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#6366f1'
        ];

        async function loadAnalytics() {
            const res = await fetch('/api/analytics');
            const data = await res.json();
            chartsLoaded = true;

            // Render totals cards
            const totals = data.totals;
            const probationYears = Math.floor(totals.total_probation_months / 12);
            const probationMonths = totals.total_probation_months % 12;
            const probationStr = probationYears > 0
                ? `${probationYears}y ${probationMonths}m`
                : `${probationMonths} months`;

            document.getElementById('totals-grid').innerHTML = `
                <div class="total-card">
                    <div class="value">$${totals.total_fines.toLocaleString()}</div>
                    <div class="label">Total Fines Collected</div>
                    <div class="sublabel">${totals.min_year}–${totals.max_year}</div>
                </div>
                <div class="total-card green">
                    <div class="value">$${totals.avg_fine_per_year.toLocaleString()}</div>
                    <div class="label">Avg Fines Per Year</div>
                    <div class="sublabel">${totals.year_span} years of data</div>
                </div>
                <div class="total-card amber">
                    <div class="value">$${totals.total_investigation_costs.toLocaleString()}</div>
                    <div class="label">Investigation Costs</div>
                    <div class="sublabel">Recovered from respondents</div>
                </div>
                <div class="total-card purple">
                    <div class="value">${totals.total_cme_hours.toLocaleString()}</div>
                    <div class="label">CME Hours Required</div>
                    <div class="sublabel">Continuing education ordered</div>
                </div>
                <div class="total-card cyan">
                    <div class="value">${probationStr}</div>
                    <div class="label">Total Probation Time</div>
                    <div class="sublabel">${totals.total_probation_months} months combined</div>
                </div>
            `;

            // Render settlement summary
            const summary = data.settlement_summary;
            document.getElementById('settlement-summary').innerHTML = `
                <div class="summary-card">
                    <div class="value">${summary.total}</div>
                    <div class="label">Total Settlements</div>
                </div>
                <div class="summary-card">
                    <div class="value">${summary.with_fine}</div>
                    <div class="label">With Fines</div>
                </div>
                <div class="summary-card">
                    <div class="value">${summary.with_probation}</div>
                    <div class="label">With Probation</div>
                </div>
                <div class="summary-card">
                    <div class="value">${summary.with_cme}</div>
                    <div class="label">CME Required</div>
                </div>
                <div class="summary-card">
                    <div class="value">${summary.public_reprimand}</div>
                    <div class="label">Public Reprimand</div>
                </div>
                <div class="summary-card">
                    <div class="value">${summary.npdb_report}</div>
                    <div class="label">NPDB Report</div>
                </div>
            `;

            // Cases by Year (line chart)
            createChart('yearChart', 'line', {
                labels: data.by_year.map(d => d.year),
                datasets: [{
                    label: 'Cases',
                    data: data.by_year.map(d => d.count),
                    borderColor: '#3b82f6',
                    backgroundColor: 'rgba(59, 130, 246, 0.1)',
                    fill: true,
                    tension: 0.3
                }]
            });

            // Category breakdown (doughnut)
            createChart('categoryChart', 'doughnut', {
                labels: data.categories.map(d => d.category),
                datasets: [{
                    data: data.categories.map(d => d.count),
                    backgroundColor: chartColors
                }]
            }, { plugins: { legend: { position: 'right' } } });

            // Specialty breakdown (horizontal bar)
            createChart('specialtyChart', 'bar', {
                labels: data.specialties.map(d => d.specialty),
                datasets: [{
                    label: 'Cases',
                    data: data.specialties.map(d => d.count),
                    backgroundColor: '#3b82f6'
                }]
            }, { indexAxis: 'y' });

            // License actions (horizontal bar)
            createChart('actionsChart', 'bar', {
                labels: data.license_actions.map(d => d.action),
                datasets: [{
                    label: 'Settlements',
                    data: data.license_actions.map(d => d.count),
                    backgroundColor: '#10b981'
                }]
            }, { indexAxis: 'y' });

            // Fine distribution (histogram) - capped at 90th percentile
            createHistogram('fineChart', data.fine_values, 'Fines', '#f59e0b', 'fine-info', '$', '', 90);

            // Cost distribution (histogram) - capped at 90th percentile
            createHistogram('costChart', data.cost_values, 'Investigation Costs', '#ef4444', 'cost-info', '$', '', 90);

            // CME hours (histogram)
            createHistogram('cmeChart', data.cme_values, 'CME Hours', '#8b5cf6', 'cme-info', '', ' hrs');

            // Probation duration (histogram)
            createHistogram('probationChart', data.probation_values, 'Probation', '#06b6d4', 'probation-info', '', ' mo');
        }

        function createChart(canvasId, type, data, extraOptions = {}) {
            const ctx = document.getElementById(canvasId).getContext('2d');
            if (charts[canvasId]) charts[canvasId].destroy();

            const options = {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: type === 'doughnut' }
                },
                ...extraOptions
            };

            charts[canvasId] = new Chart(ctx, { type, data, options });
        }

        function createHistogram(canvasId, values, label, color, infoId, prefix = '', suffix = '', percentileCap = null) {
            if (!values || values.length === 0) {
                document.getElementById(infoId).innerHTML = '<span>No data available</span>';
                return;
            }

            // Calculate histogram bins
            const sorted = [...values].sort((a, b) => a - b);
            const min = sorted[0];
            const max = sorted[sorted.length - 1];
            const mean = values.reduce((a, b) => a + b, 0) / values.length;
            const median = sorted[Math.floor(sorted.length / 2)];

            // Calculate percentile cap if specified
            let capValue = max;
            let aboveCapCount = 0;
            if (percentileCap && percentileCap < 100) {
                const capIndex = Math.floor(sorted.length * percentileCap / 100);
                capValue = sorted[capIndex];
                aboveCapCount = sorted.length - capIndex - 1;
            }

            // Create bins up to cap value
            const numBins = Math.min(9, Math.ceil(Math.sqrt(values.length))); // Leave room for overflow bin
            const binSize = (capValue - min) / numBins || 1;
            const bins = Array(numBins + (aboveCapCount > 0 ? 1 : 0)).fill(0);
            const binLabels = [];

            for (let i = 0; i < numBins; i++) {
                const binStart = min + i * binSize;
                binLabels.push(`${prefix}${Math.round(binStart).toLocaleString()}${suffix}`);
            }

            // Add overflow bucket label if needed
            if (aboveCapCount > 0) {
                binLabels.push(`>${prefix}${Math.round(capValue).toLocaleString()}${suffix}`);
            }

            values.forEach(v => {
                if (percentileCap && v > capValue) {
                    bins[bins.length - 1]++; // Put in overflow bucket
                } else {
                    const binIndex = Math.min(Math.floor((v - min) / binSize), numBins - 1);
                    bins[binIndex]++;
                }
            });

            createChart(canvasId, 'bar', {
                labels: binLabels,
                datasets: [{
                    label: label,
                    data: bins,
                    backgroundColor: bins.map((_, i) => i === bins.length - 1 && aboveCapCount > 0 ? '#9ca3af' : color)
                }]
            });

            const capNote = percentileCap ? ` (90th percentile: ${prefix}${Math.round(capValue).toLocaleString()}${suffix})` : '';
            document.getElementById(infoId).innerHTML = `
                <span>Min: ${prefix}${min.toLocaleString()}${suffix}</span>
                <span>Median: ${prefix}${Math.round(median).toLocaleString()}${suffix}</span>
                <span>Mean: ${prefix}${Math.round(mean).toLocaleString()}${suffix}</span>
                <span>Max: ${prefix}${max.toLocaleString()}${suffix}</span>
            `;
        }

        // Initialize
        loadFilters();
        loadStats();
        search();
    </script>
</body>
</html>
"""


@app.get("/api/stats")
def get_stats():
    """Get overall statistics."""
    settlements_coll = db["settlements"]

    total = complaints.count_documents({})
    with_extraction = complaints.count_documents({"llm_extracted": {"$exists": True}})

    # Count settlements
    total_settlements = settlements_coll.count_documents({})
    settlements_with_extraction = settlements_coll.count_documents({"llm_extracted": {"$exists": True}})

    # Count unique categories
    categories = complaints.distinct("llm_extracted.category")
    categories = [c for c in categories if c]

    # Count unique drugs
    drugs_pipeline = [
        {"$unwind": "$llm_extracted.drugs"},
        {"$group": {"_id": "$llm_extracted.drugs"}},
        {"$count": "count"}
    ]
    drugs_result = list(complaints.aggregate(drugs_pipeline))
    drugs_count = drugs_result[0]["count"] if drugs_result else 0

    return {
        "total": total,
        "with_extraction": with_extraction,
        "settlements": settlements_with_extraction,
        "categories": len(categories),
        "drugs": drugs_count
    }


@app.get("/api/filters")
def get_filters():
    """Get available filter options."""
    categories = complaints.distinct("llm_extracted.category")
    categories = sorted([c for c in categories if c])

    specialties = complaints.distinct("llm_extracted.specialty")
    specialties = sorted([s for s in specialties if s])

    years = complaints.distinct("year")
    years = sorted([y for y in years if y], reverse=True)

    # Get all drugs mentioned
    drugs_pipeline = [
        {"$unwind": "$llm_extracted.drugs"},
        {"$group": {"_id": {"$toLower": "$llm_extracted.drugs"}, "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 100}
    ]
    drugs_result = list(complaints.aggregate(drugs_pipeline))
    drugs = [d["_id"] for d in drugs_result]

    return {
        "categories": categories,
        "specialties": specialties,
        "years": years,
        "drugs": drugs
    }


@app.get("/api/complaints")
def get_complaints(
    category: Optional[str] = None,
    specialty: Optional[str] = None,
    year: Optional[int] = None,
    drug: Optional[str] = None,
    sex: Optional[str] = None,
    has_settlement: Optional[str] = None,
    sort: str = "date_desc",
    skip: int = 0,
    limit: int = Query(default=20, le=100)
):
    """Get complaints with filtering and sorting."""
    settlements_coll = db["settlements"]

    query = {"llm_extracted": {"$exists": True}}

    if category:
        query["llm_extracted.category"] = category
    if specialty:
        query["llm_extracted.specialty"] = specialty
    if year:
        query["year"] = year
    if drug:
        query["llm_extracted.drugs"] = {"$regex": drug, "$options": "i"}
    if sex:
        query["llm_extracted.complainants.sex"] = sex

    # Filter by settlement existence
    if has_settlement:
        # Get all case numbers that have settlements
        settlement_case_numbers = set(
            doc["case_number"] for doc in settlements_coll.find({}, {"case_number": 1})
        )
        if has_settlement == "yes":
            query["case_number"] = {"$in": list(settlement_case_numbers)}
        elif has_settlement == "no":
            query["case_number"] = {"$nin": list(settlement_case_numbers)}

    # Sorting
    sort_field = "date"
    sort_dir = -1
    if sort == "date_asc":
        sort_field = "date"
        sort_dir = 1
    elif sort == "year_desc":
        sort_field = "year"
        sort_dir = -1
    elif sort == "year_asc":
        sort_field = "year"
        sort_dir = 1
    elif sort == "respondent":
        sort_field = "respondent"
        sort_dir = 1

    total = complaints.count_documents(query)

    cursor = complaints.find(
        query,
        {"text_content": 0}  # Exclude large text field
    ).sort(sort_field, sort_dir).skip(skip).limit(limit)

    results = []
    for doc in cursor:
        doc["_id"] = str(doc["_id"])
        results.append(doc)

    return {"complaints": results, "total": total}


@app.get("/api/random")
def get_random():
    """Get a random complaint."""
    pipeline = [
        {"$match": {"llm_extracted": {"$exists": True}}},
        {"$sample": {"size": 1}},
        {"$project": {"text_content": 0}}
    ]
    result = list(complaints.aggregate(pipeline))
    if result:
        result[0]["_id"] = str(result[0]["_id"])
        return result[0]
    return {"error": "No complaints found"}


@app.get("/api/complaint/{case_number}")
def get_complaint(case_number: str):
    """Get a specific complaint by case number."""
    doc = complaints.find_one({"case_number": case_number})
    if doc:
        doc["_id"] = str(doc["_id"])
        return doc
    return {"error": "Complaint not found"}


@app.get("/api/settlement/{case_number}")
def get_settlement(case_number: str):
    """Get a settlement by case number."""
    settlements = db["settlements"]
    doc = settlements.find_one({"case_number": case_number})
    if doc:
        doc["_id"] = str(doc["_id"])
        if doc.get("complaint_id"):
            doc["complaint_id"] = str(doc["complaint_id"])
        return doc
    return None


@app.get("/api/analytics")
def get_analytics():
    """Get aggregate analytics data for charts."""
    settlements_coll = db["settlements"]

    # Fine amounts distribution
    fines_pipeline = [
        {"$match": {"llm_extracted.fine_amount": {"$exists": True, "$ne": None, "$gt": 0}}},
        {"$group": {
            "_id": None,
            "values": {"$push": "$llm_extracted.fine_amount"}
        }}
    ]
    fines_result = list(settlements_coll.aggregate(fines_pipeline))
    fine_values = fines_result[0]["values"] if fines_result else []

    # Investigation costs distribution
    costs_pipeline = [
        {"$match": {"llm_extracted.investigation_costs": {"$exists": True, "$ne": None, "$gt": 0}}},
        {"$group": {
            "_id": None,
            "values": {"$push": "$llm_extracted.investigation_costs"}
        }}
    ]
    costs_result = list(settlements_coll.aggregate(costs_pipeline))
    cost_values = costs_result[0]["values"] if costs_result else []

    # CME hours distribution
    cme_pipeline = [
        {"$match": {"llm_extracted.cme_hours": {"$exists": True, "$ne": None, "$gt": 0}}},
        {"$group": {
            "_id": None,
            "values": {"$push": "$llm_extracted.cme_hours"}
        }}
    ]
    cme_result = list(settlements_coll.aggregate(cme_pipeline))
    cme_values = cme_result[0]["values"] if cme_result else []

    # Probation months distribution
    probation_pipeline = [
        {"$match": {"llm_extracted.probation_months": {"$exists": True, "$ne": None, "$gt": 0}}},
        {"$group": {
            "_id": None,
            "values": {"$push": "$llm_extracted.probation_months"}
        }}
    ]
    probation_result = list(settlements_coll.aggregate(probation_pipeline))
    probation_values = probation_result[0]["values"] if probation_result else []

    # License actions breakdown
    actions_pipeline = [
        {"$match": {"llm_extracted.license_action": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": "$llm_extracted.license_action", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 15}
    ]
    actions_result = list(settlements_coll.aggregate(actions_pipeline))

    # Specialty breakdown (from complaints)
    specialty_pipeline = [
        {"$match": {"llm_extracted.specialty": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": "$llm_extracted.specialty", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 15}
    ]
    specialty_result = list(complaints.aggregate(specialty_pipeline))

    # Category breakdown (from complaints)
    category_pipeline = [
        {"$match": {"llm_extracted.category": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": "$llm_extracted.category", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}
    ]
    category_result = list(complaints.aggregate(category_pipeline))

    # Cases by year
    year_pipeline = [
        {"$match": {"year": {"$exists": True}}},
        {"$group": {"_id": "$year", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}}
    ]
    year_result = list(complaints.aggregate(year_pipeline))

    # Settlement outcomes summary
    settlement_summary = {
        "total": settlements_coll.count_documents({}),
        "with_fine": settlements_coll.count_documents({"llm_extracted.fine_amount": {"$gt": 0}}),
        "with_probation": settlements_coll.count_documents({"llm_extracted.probation_months": {"$gt": 0}}),
        "with_cme": settlements_coll.count_documents({"llm_extracted.cme_hours": {"$gt": 0}}),
        "public_reprimand": settlements_coll.count_documents({"llm_extracted.public_reprimand": True}),
        "npdb_report": settlements_coll.count_documents({"llm_extracted.npdb_report": True}),
    }

    # Calculate totals
    totals_pipeline = [
        {"$group": {
            "_id": None,
            "total_fines": {"$sum": {"$ifNull": ["$llm_extracted.fine_amount", 0]}},
            "total_investigation_costs": {"$sum": {"$ifNull": ["$llm_extracted.investigation_costs", 0]}},
            "total_cme_hours": {"$sum": {"$ifNull": ["$llm_extracted.cme_hours", 0]}},
            "total_probation_months": {"$sum": {"$ifNull": ["$llm_extracted.probation_months", 0]}},
        }}
    ]
    totals_result = list(settlements_coll.aggregate(totals_pipeline))
    totals = totals_result[0] if totals_result else {}

    # Fines by year
    fines_by_year_pipeline = [
        {"$match": {"llm_extracted.fine_amount": {"$gt": 0}}},
        {"$group": {
            "_id": "$year",
            "total": {"$sum": "$llm_extracted.fine_amount"},
            "count": {"$sum": 1}
        }},
        {"$sort": {"_id": 1}}
    ]
    fines_by_year = list(settlements_coll.aggregate(fines_by_year_pipeline))

    # Calculate years span
    years = [r["_id"] for r in year_result if r["_id"]]
    year_span = max(years) - min(years) + 1 if years else 1

    return {
        "fine_values": fine_values,
        "cost_values": cost_values,
        "cme_values": cme_values,
        "probation_values": probation_values,
        "license_actions": [{"action": r["_id"], "count": r["count"]} for r in actions_result],
        "specialties": [{"specialty": r["_id"], "count": r["count"]} for r in specialty_result],
        "categories": [{"category": r["_id"], "count": r["count"]} for r in category_result],
        "by_year": [{"year": r["_id"], "count": r["count"]} for r in year_result],
        "fines_by_year": [{"year": r["_id"], "total": r["total"], "count": r["count"]} for r in fines_by_year],
        "settlement_summary": settlement_summary,
        "totals": {
            "total_fines": totals.get("total_fines", 0),
            "total_investigation_costs": totals.get("total_investigation_costs", 0),
            "total_cme_hours": totals.get("total_cme_hours", 0),
            "total_probation_months": totals.get("total_probation_months", 0),
            "avg_fine_per_year": round(totals.get("total_fines", 0) / year_span) if year_span else 0,
            "total_complaints": complaints.count_documents({}),
            "year_span": year_span,
            "min_year": min(years) if years else None,
            "max_year": max(years) if years else None,
        }
    }
