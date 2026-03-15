// ==========================================
// UTILITY: HTML Escaper (Fix #1 — XSS prevention)
// ==========================================
function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    const s = String(str);
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(s));
    return div.innerHTML;
}

// ==========================================
// UTILITY: Loading Spinner (Fix #10)
// ==========================================
function showSpinner(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    // Don't overwrite innerHTML, just prepend the spinner (Fix for #null-refs)
    if (!el.querySelector('.spinner-container')) {
        const div = document.createElement('div');
        div.className = 'spinner-container';
        div.style.width = '100%';
        div.innerHTML = '<div class="spinner"></div><p class="text-muted">Loading...</p>';
        el.prepend(div);
    }
}

function hideSpinner(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    const spinner = el.querySelector('.spinner-container');
    if (spinner) spinner.remove();
}

// ==========================================
// GLOBAL STATE
// ==========================================
const state = {
    conferences: [],
    journals: [],
    selectedConf: null,
    selectedJournal: null,
    selectedAuthor: null,
    compareEntities: [], // stores {type, id, title, color} for line chart comparison
    scatterData: null // stores {scatter: [...]} for the scatter plot
};

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    const path = window.location.pathname;
    
    // Global generic fetchers (only for pages that still need them or small lists)
    if (path === '/charts') {
        loadVenuesList('conference');
        loadVenuesList('journal');
    }

    // Attach Event Listeners based on current page
    if (path === '/conference') initConferencePage();
    if (path === '/journal') initJournalPage();
    if (path === '/author') initAuthorPage();
    if (path === '/year') initYearPage();
    if (path === '/charts') initChartsPage();
    if (path === '/trends') initTrendsPage();
    
    // Dashboard real chart on index page
    if (path === '/' && document.getElementById('chart')) {
        loadDashboardChart();
    }
});

// --- Fetch & Store Baseline Data ---
async function loadVenuesList(type) {
    try {
        const res = await fetch(`/api/${type}/`);
        const data = await res.json();
        
        if (type === 'conference') state.conferences = data;
        if (type === 'journal') state.journals = data;
    } catch(err) {
        console.error(`Failed to load ${type}s:`, err);
    }
}

function setupAutocomplete(config) {
    // config: { inputId, dropdownId, dataSource, filterFn, displayFn, onSelect }
    const input = document.getElementById(config.inputId);
    const dropdown = document.getElementById(config.dropdownId);
    if (!input || !dropdown) return;
    
    input.addEventListener('input', async (e) => {
        const query = e.target.value.toLowerCase();
        dropdown.innerHTML = '';
        if (query.length < 2) {
            dropdown.style.display = 'none';
            return;
        }

        let matches;
        if (typeof config.dataSource === 'function') {
            // Server-side search (e.g., authors)
            matches = await config.dataSource(query);
        } else {
            // Client-side filter
            matches = config.filterFn(config.dataSource(), query).slice(0, 10);
        }

        if (matches && matches.length > 0) {
            dropdown.style.display = 'block';
            matches.forEach(match => {
                const li = document.createElement('li');
                li.textContent = config.displayFn(match);
                li.onclick = () => {
                    input.value = config.displayFn(match);
                    dropdown.style.display = 'none';
                    config.onSelect(match);
                };
                dropdown.appendChild(li);
            });
        } else {
            dropdown.style.display = 'none';
        }
    });

    // Close dropdown when clicking outside
    document.addEventListener('click', (e) => {
        if (e.target !== input) dropdown.style.display = 'none';
    });
}

// ==========================================
// CONFERENCE PAGE LOGIC
// ==========================================
function initConferencePage() {
    setupAutocomplete({
        inputId: 'conferenceSearch',
        dropdownId: 'conferenceDropdown',
        dataSource: async (q) => {
            const res = await fetch(`/api/conference/search?q=${encodeURIComponent(q)}`);
            return await res.json();
        },
        displayFn: (match) => match.acronym ? `${match.acronym} - ${match.title}` : match.title,
        onSelect: (match) => loadConferenceProfile(match.conf_id)
    });
    
    const applyFiltersBtn = document.getElementById('applyFilters');
    if(applyFiltersBtn) {
        applyFiltersBtn.addEventListener('click', () => {
            if (state.selectedConf) {
                loadConferencePapers(state.selectedConf);
            }
        });
    }
}

async function loadConferenceProfile(id) {
    state.selectedConf = id;
    showSpinner('dashboardGrid');
    try {
        const res = await fetch(`/api/conference/${id}/profile`);
        const data = await res.json();
        
        if (data.error) return alert("Profile not found.");

        const p = data.profile;
        document.getElementById('confTitle').textContent = p.title || 'Unknown Title';
        document.getElementById('confRank').textContent = `Rank: ${p.rank || 'N/A'}`;
        document.getElementById('confAcronym').textContent = p.acronym;
        document.getElementById('confCategory').textContent = p.for_description || 'No Base Category';
        document.getElementById('confDates').textContent = `Active: ${p.first_year || '?'} - ${p.last_year || '?'} | Avg Papers/Year: ${Math.round(p.avg_papers_per_year) || 0} | Total Distinct Authors: ${p.total_distinct_authors || p.distinct_authors || 0}`;

        document.getElementById('conferenceDetails').style.display = 'block';
        document.getElementById('filtersSection').style.display = 'block';
        document.getElementById('dashboardGrid').style.display = 'flex';

        // Populate Stat Cards
        document.getElementById('statTotalPapers').textContent = p.total_papers || 0;
        document.getElementById('statTotalAuthors').textContent = p.distinct_authors || 0;
        document.getElementById('statYears').textContent = p.first_year ? `${p.first_year} - ${p.last_year}` : '-';
        document.getElementById('statAvgAuthors').textContent = p.avg_authors_per_paper || 0;

        hideSpinner('dashboardGrid');

        // Plot Charts
        if (window.renderConfJournalCharts) {
            window.renderConfJournalCharts(data.yearly_stats);
        }

        // Load papers table and top authors
        loadConferencePapers(id);
        loadConferenceTopAuthors(id);

    } catch(err) {
        hideSpinner('dashboardGrid');
        console.error(err);
    }
}

async function loadConferencePapers(id) {
    const startObj = document.getElementById('startYear');
    const endObj = document.getElementById('endYear');
    const start = startObj ? startObj.value : '';
    const end = endObj ? endObj.value : '';
    let url = `/api/conference/${id}/papers?`;
    if (start) url += `start_year=${start}&`;
    if (end) url += `end_year=${end}`;

    try {
        const res = await fetch(url);
        const data = await res.json();
        const tbody = document.querySelector('#papersTable tbody');
        if(!tbody) return;
        tbody.innerHTML = '';

        data.papers.forEach(p => {
            const tr = document.createElement('tr');
            const tdYear = document.createElement('td');
            tdYear.textContent = p.year;
            const tdTitle = document.createElement('td');
            tdTitle.textContent = p.title;
            const tdPages = document.createElement('td');
            tdPages.textContent = p.pages || '-';
            const tdLinks = document.createElement('td');
            if (p.ee) {
                const eeLink = document.createElement('a');
                eeLink.href = p.ee;
                eeLink.target = '_blank';
                eeLink.textContent = 'EE';
                tdLinks.appendChild(eeLink);
                tdLinks.appendChild(document.createTextNode(' '));
            }
            if (p.url) {
                const dblpLink = document.createElement('a');
                dblpLink.href = p.url;
                dblpLink.target = '_blank';
                dblpLink.textContent = 'DBLP';
                tdLinks.appendChild(dblpLink);
            }
            tr.append(tdYear, tdTitle, tdPages, tdLinks);
            tbody.appendChild(tr);
        });
    } catch(err) {
        console.error(err);
    }
}

async function loadConferenceTopAuthors(id) {
    try {
        const res = await fetch(`/api/conference/${id}/top_authors?limit=10`);
        const data = await res.json();
        const tbody = document.querySelector('#topAuthorsTable tbody');
        if(!tbody) return;
        tbody.innerHTML = '';

        if(data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding: 2rem; color: var(--text-muted);">No authors found</td></tr>';
            return;
        }

        data.forEach((author, index) => {
            const tr = document.createElement('tr');
            
            const tdRank = document.createElement('td');
            const badge = document.createElement('span');
            badge.className = 'badge rank-badge';
            badge.textContent = `#${index + 1}`;
            tdRank.appendChild(badge);
            
            const tdName = document.createElement('td');
            tdName.textContent = author.name;
            tdName.style.fontWeight = 'bold';
            
            const tdCount = document.createElement('td');
            tdCount.textContent = author.paper_count;
            
            const tdAction = document.createElement('td');
            tdAction.innerHTML = `<span style="font-size: 0.85rem; color: var(--text-muted);">Search in Authors tab</span>`;
            
            tr.append(tdRank, tdName, tdCount, tdAction);
            tbody.appendChild(tr);
        });
    } catch(err) {
        console.error(err);
    }
}

// ==========================================
// JOURNAL PAGE LOGIC
// ==========================================
function initJournalPage() {
    setupAutocomplete({
        inputId: 'journalSearch',
        dropdownId: 'journalDropdown',
        dataSource: async (q) => {
            const res = await fetch(`/api/journal/search?q=${encodeURIComponent(q)}`);
            return await res.json();
        },
        displayFn: (match) => match.title,
        onSelect: (match) => loadJournalProfile(match.journal_id)
    });
    
    const applyFiltersBtn = document.getElementById('applyFilters');
    if(applyFiltersBtn) {
        applyFiltersBtn.addEventListener('click', () => {
            if (state.selectedJournal) {
                loadJournalPapers(state.selectedJournal);
            }
        });
    }
}

async function loadJournalProfile(id) {
    state.selectedJournal = id;
    showSpinner('dashboardGrid');
    try {
        const res = await fetch(`/api/journal/${id}/profile`);
        const data = await res.json();
        
        if (data.error) return alert("Profile not found.");

        const p = data.profile;
        document.getElementById('journalTitle').textContent = p.title || 'Unknown Title';
        const rankEl = document.getElementById('journalRank');
        rankEl.textContent = `Quartile: ${p.best_quartile || 'N/A'}`;
        // Color coding for Quartiles (Fix #13)
        if (p.best_quartile === 'Q1') rankEl.style.background = '#10b981';
        else if (p.best_quartile === 'Q2') rankEl.style.background = '#fde047';
        else if (p.best_quartile === 'Q3') rankEl.style.background = '#f97316';
        else if (p.best_quartile === 'Q4') rankEl.style.background = '#ef4444';

        document.getElementById('journalSjr').textContent = `SJR: ${p.sjr_index || 0}`;
        document.getElementById('journalCiteScore').textContent = `CiteScore: ${p.cite_score || 0}`;
        document.getElementById('journalHIndex').textContent = `H-Index: ${p.h_index || 0}`;
        document.getElementById('journalPublisher').textContent = p.publisher || 'Unknown Publisher';
        document.getElementById('journalArea').textContent = p.subject_area || 'No Subject Area';
        
        let statsText = `Active: ${p.first_year || '?'} - ${p.last_year || '?'} | Avg Papers/Year: ${Math.round(p.avg_papers_per_year) || 0} | Total Distinct Authors: ${p.total_distinct_authors || p.distinct_authors || 0}`;
        document.getElementById('journalStats').textContent = statsText;

        document.getElementById('journalCollab').textContent = `Collaboration: Avg ${p.avg_authors_per_paper || 0} authors per paper`;

        document.getElementById('journalDetails').style.display = 'block';
        document.getElementById('filtersSection').style.display = 'block';
        document.getElementById('dashboardGrid').style.display = 'flex';

        hideSpinner('dashboardGrid');

        if (window.renderConfJournalCharts) {
            window.renderConfJournalCharts(data.yearly_stats);
        }

        loadJournalPapers(id);

    } catch(err) {
        hideSpinner('dashboardGrid');
        console.error(err);
    }
}

async function loadJournalPapers(id) {
    const startObj = document.getElementById('startYear');
    const endObj = document.getElementById('endYear');
    const start = startObj ? startObj.value : '';
    const end = endObj ? endObj.value : '';
    let url = `/api/journal/${id}/papers?`;
    if (start) url += `start_year=${start}&`;
    if (end) url += `end_year=${end}`;

    try {
        const res = await fetch(url);
        const data = await res.json();
        const tbody = document.querySelector('#papersTable tbody');
        if(!tbody) return;
        tbody.innerHTML = '';

        data.papers.forEach(p => {
            const tr = document.createElement('tr');
            const tdYear = document.createElement('td');
            tdYear.textContent = p.year;
            const tdTitle = document.createElement('td');
            tdTitle.textContent = p.title;
            const tdVol = document.createElement('td');
            tdVol.textContent = `Vol ${p.volume || '-'} (${p.number || '-'})`;
            const tdPages = document.createElement('td');
            tdPages.textContent = p.pages || '-';
            const tdLinks = document.createElement('td');
            if (p.ee) {
                const eeLink = document.createElement('a');
                eeLink.href = p.ee;
                eeLink.target = '_blank';
                eeLink.textContent = 'EE';
                tdLinks.appendChild(eeLink);
            }
            tr.append(tdYear, tdTitle, tdVol, tdPages, tdLinks);
            tbody.appendChild(tr);
        });
    } catch(err) {
        console.error(err);
    }
}

let authorSearchDebounce = null;

function initAuthorPage() {
    const input = document.getElementById('authorSearch');
    if (!input) return;

    // Create dropdown for author search results
    let dropdown = document.getElementById('authorDropdown');
    if (!dropdown) {
        dropdown = document.createElement('ul');
        dropdown.id = 'authorDropdown';
        dropdown.className = 'autocomplete-list';
        input.parentElement.appendChild(dropdown);
    }

    input.addEventListener('input', (e) => {
        const query = e.target.value.trim();
        if (authorSearchDebounce) clearTimeout(authorSearchDebounce);
        
        if (query.length < 3) {
            dropdown.style.display = 'none';
            dropdown.innerHTML = '';
            return;
        }

        // Debounce 300ms to avoid flooding server
        authorSearchDebounce = setTimeout(async () => {
            try {
                const res = await fetch(`/api/author/search?q=${encodeURIComponent(query)}`);
                const results = await res.json();
                dropdown.innerHTML = '';
                
                if (results.length > 0) {
                    dropdown.style.display = 'block';
                    results.forEach(author => {
                        const li = document.createElement('li');
                        li.textContent = author.name;
                        li.onclick = () => {
                            input.value = author.name;
                            dropdown.style.display = 'none';
                            loadAuthorProfile(author.author_id);
                        };
                        dropdown.appendChild(li);
                    });
                } else {
                    dropdown.style.display = 'none';
                }
            } catch(err) {
                console.error('Author search error:', err);
            }
        }, 300);
    });

    document.addEventListener('click', (e) => {
        if (e.target !== input) dropdown.style.display = 'none';
    });
}

async function loadAuthorProfile(authorId) {
    state.selectedAuthor = authorId;
    showSpinner('dashboardGrid');
    try {
        const res = await fetch(`/api/author/${authorId}/profile`);
        const data = await res.json();
        
        if (data.error) return alert("Author not found.");

        const p = data.profile;
        document.getElementById('authorName').textContent = p.name || 'Unknown';
        document.getElementById('authorTotalPapers').textContent = `Total Papers: ${p.total_papers || 0}`;
        document.getElementById('authorYearsActive').textContent = `Active: ${p.first_year || '?'} - ${p.last_year || '?'}`;
        document.getElementById('authorAvgPapers').textContent = `Avg Papers/Year: ${p.avg_papers_per_year ? Number(p.avg_papers_per_year).toFixed(1) : '0.0'}`;

        document.getElementById('authorDetails').style.display = 'block';
        document.getElementById('dashboardGrid').style.display = 'flex';

        hideSpinner('dashboardGrid');

        // Render charts
        if (window.renderAuthorCharts) {
            window.renderAuthorCharts(data.yearly_stats);
        }

        // Load papers table
        const pres = await fetch(`/api/author/${authorId}/papers`);
        const pdata = await pres.json();
        const tbody = document.querySelector('#papersTable tbody');
        if(!tbody) return;
        tbody.innerHTML = '';

        pdata.papers.forEach(p => {
            const tr = document.createElement('tr');
            const tdYear = document.createElement('td');
            tdYear.textContent = p.year;
            const tdType = document.createElement('td');
            const typeBadge = document.createElement('span');
            typeBadge.className = `badge ${p.type === 'conference' ? 'rank-badge' : 'category-badge'}`;
            typeBadge.textContent = p.type;
            tdType.appendChild(typeBadge);
            const tdTitle = document.createElement('td');
            tdTitle.textContent = p.title;
            const tdVenue = document.createElement('td');
            tdVenue.textContent = p.conf_acronym || p.journal_title || 'Unknown';
            tr.append(tdYear, tdType, tdTitle, tdVenue);
            tbody.appendChild(tr);
        });

    } catch(err) {
        hideSpinner('dashboardGrid');
        console.error(err);
    }
}

// ==========================================
// YEAR PAGE LOGIC
// ==========================================
function initYearPage() {
    const input = document.getElementById('yearSearch');
    if(!input) return;
    input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            loadYearProfile(input.value);
        }
    });
}

async function loadYearProfile(year) {
    showSpinner('dashboardGrid');
    try {
        const res = await fetch(`/api/year/${year}/profile`);
        const data = await res.json();
        
        if (data.error) return alert("Year not found in database.");

        document.getElementById('yearTitle').textContent = year;
        document.getElementById('yearTotalPapers').textContent = `Total Papers: ${data.total_papers || 0}`;
        document.getElementById('yearTotalAuthors').textContent = `Total Authors: ${data.total_authors || 0}`;
        document.getElementById('yearDistJournals').textContent = `Distinct Journals: ${data.distinct_journals || 0}`;
        document.getElementById('yearDistConfs').textContent = `Distinct Conferences: ${data.distinct_conferences || 0}`;

        document.getElementById('yearDetails').style.display = 'block';
        document.getElementById('dashboardGrid').style.display = 'flex';

        hideSpinner('dashboardGrid');

        // Fetch Papers
        const pres = await fetch(`/api/year/${year}/papers?limit=250`);
        const pdata = await pres.json();
        const tbody = document.querySelector('#papersTable tbody');
        if(!tbody) return;
        tbody.innerHTML = '';

        pdata.papers.forEach(p => {
            const tr = document.createElement('tr');
            const tdType = document.createElement('td');
            const typeBadge = document.createElement('span');
            typeBadge.className = `badge ${p.type === 'conference' ? 'rank-badge' : 'category-badge'}`;
            typeBadge.textContent = p.type;
            tdType.appendChild(typeBadge);
            const tdTitle = document.createElement('td');
            tdTitle.textContent = p.title;
            const tdVenue = document.createElement('td');
            tdVenue.textContent = p.venue_name || 'Unknown';
            tr.append(tdType, tdTitle, tdVenue);
            tbody.appendChild(tr);
        });

    } catch(err) {
        hideSpinner('dashboardGrid');
        console.error(err);
    }
}

// ==========================================
// DASHBOARD: Real Data Homepage Chart (Fix #11)
// ==========================================
async function loadDashboardChart() {
    try {
        const res = await fetch('/api/charts/overview');
        const data = await res.json();
        if (data.yearly_totals && data.yearly_totals.length > 0) {
            if (window.drawAnimatedLineChart) {
                drawAnimatedLineChart('#chart', data.yearly_totals, 'year', 'num_papers', "#38bdf8");
            }
        } else {
            // Fallback to sample data if endpoint not ready
            if(window.renderSampleChart) renderSampleChart();
        }
    } catch(err) {
        // Fallback to sample data on error
        if(window.renderSampleChart) renderSampleChart();
    }
}

// ==========================================
// ADVANCED TARGETED CHARTS PAGE LOGIC
// ==========================================
function initChartsPage() {
    setupComparisonAutocomplete();
    
    document.getElementById('clearComparisonBtn').addEventListener('click', () => {
        state.compareEntities = [];
        updateComparisonUI();
    });

    loadPublisherBarChart(); // Initialize new bar chart
    loadScatterPlot(); // Initialize scatter plot
}

async function loadPublisherBarChart() {
    try {
        const res = await fetch('/api/charts/publishers/bar');
        const data = await res.json();
        const spinner = document.getElementById('publisherSpinner');
        if (spinner) spinner.style.display = 'none';

        if (data.publishers && data.publishers.length > 0 && window.drawBarChart) {
            drawBarChart('#publisherChart', data.publishers, 'publisher', 
                ['q1_count', 'q2_count', 'q3_count', 'q4_count'], 
                { 
                    colors: ['#10b981', '#fde047', '#f97316', '#ef4444'], 
                    legend: true, 
                    grouped: true,
                    labelFormatter: (key) => key.replace('_count', '').toUpperCase()
                }
            );
        } else {
            document.getElementById('publisherChart').innerHTML = '<p class="chart-no-data">No publisher data available.</p>';
        }
    } catch (err) {
        console.error("Failed to load publisher bar chart:", err);
        document.getElementById('publisherChart').innerHTML = '<p class="chart-error">Failed to load data.</p>';
    }
}

async function loadScatterPlot() {
    const spinner = document.getElementById('scatterSpinner');
    const chartDiv = document.getElementById('scatterChart');
    if (spinner) spinner.style.display = 'flex';

    if (!state.scatterData) {
        try {
            const res = await fetch('/api/charts/scatter/metrics');
            const data = await res.json();
            if (data.error) throw new Error(data.error);
            state.scatterData = data.scatter || [];
        } catch (err) {
            console.error("Failed to load scatter plot data:", err);
            if (spinner) spinner.style.display = 'none';
            chartDiv.innerHTML = '<p class="chart-error">Failed to load scatter plot data.</p>';
            return;
        }
    }

    if (spinner) spinner.style.display = 'none';

    if (state.scatterData.length === 0) {
        chartDiv.innerHTML = '<p class="chart-no-data">No data available for scatter plot.</p>';
        return;
    }

    // Render immediately based on selected metrics
    renderScatterPlot();

    // Bind event listeners to dropdowns (unbind first to prevent multiple firings)
    const xSelect = document.getElementById('scatterX');
    const ySelect = document.getElementById('scatterY');
    
    if (xSelect) {
        xSelect.removeEventListener('change', renderScatterPlot);
        xSelect.addEventListener('change', renderScatterPlot);
    }
    if (ySelect) {
        ySelect.removeEventListener('change', renderScatterPlot);
        ySelect.addEventListener('change', renderScatterPlot);
    }
}

function renderScatterPlot() {
    const xKey = document.getElementById('scatterX')?.value || 'total_docs';
    const yKey = document.getElementById('scatterY')?.value || 'sjr_index';
    
    if (window.drawScatterPlot && state.scatterData && state.scatterData.length > 0) {
        window.drawScatterPlot('#scatterChart', state.scatterData, xKey, yKey);
    }
}

function setupComparisonAutocomplete() {
    const input = document.getElementById('addEntitySearch');
    const dropdown = document.getElementById('addEntityDropdown');
    const typeSel = document.getElementById('entityTypeSel');
    if(!input || !dropdown || !typeSel) return;
    
    input.addEventListener('input', (e) => {
        const query = e.target.value.toLowerCase();
        const type = typeSel.value;
        dropdown.innerHTML = '';
        if (query.length < 2) {
            dropdown.style.display = 'none';
            return;
        }

        const dataList = type === 'conference' ? state.conferences : state.journals;
        
        const matches = dataList.filter(item => {
            if (type === 'conference') {
                return (item.acronym && item.acronym.toLowerCase().includes(query)) || 
                       (item.title && item.title.toLowerCase().includes(query));
            } else {
                return item.title && item.title.toLowerCase().includes(query);
            }
        }).slice(0, 10);

        if (matches.length > 0) {
            dropdown.style.display = 'block';
            matches.forEach(match => {
                const li = document.createElement('li');
                const titleStr = type === 'conference' ? `[${match.acronym}] ${match.title}` : match.title;
                const id = type === 'conference' ? match.conf_id : match.journal_id;
                li.textContent = titleStr;
                li.onclick = () => {
                    input.value = '';
                    dropdown.style.display = 'none';
                    addEntityToComparison(type, id, titleStr);
                };
                dropdown.appendChild(li);
            });
        } else {
            dropdown.style.display = 'none';
        }
    });

    document.addEventListener('click', (e) => {
        if (e.target !== input) dropdown.style.display = 'none';
    });
}

function addEntityToComparison(type, id, title) {
    const scheme = ["#2563eb", "#38bdf8", "#fde047", "#f43f5e", "#10b981", "#8b5cf6"];
    
    if (state.compareEntities.length >= 6) {
        alert("Maximum 6 entities allowed in comparison.");
        return;
    }
    
    if (state.compareEntities.some(e => e.id === id && e.type === type)) {
        return;
    }
    
    const color = scheme[state.compareEntities.length];
    state.compareEntities.push({ type, id, title, color });
    updateComparisonUI();
}

function removeEntityFromComparison(idx) {
    state.compareEntities.splice(idx, 1);
    updateComparisonUI();
}

async function updateComparisonUI() {
    const list = document.getElementById('selectedEntitiesList');
    list.innerHTML = '';
    
    state.compareEntities.forEach((ent, i) => {
        const badge = document.createElement('span');
        badge.className = `badge ${ent.type === 'conference' ? 'rank-badge' : 'category-badge'}`;
        badge.style.borderLeft = `5px solid ${ent.color}`;
        badge.style.cursor = 'default';
        
        const labelText = ent.title.length > 30 ? ent.title.substring(0, 30) + '...' : ent.title;
        badge.appendChild(document.createTextNode(labelText + ' '));
        
        const removeBtn = document.createElement('b');
        removeBtn.textContent = '✖';
        removeBtn.style.cssText = 'cursor:pointer; margin-left: 5px; color: #ff5555;';
        removeBtn.onclick = () => removeEntityFromComparison(i);
        badge.appendChild(removeBtn);
        
        list.appendChild(badge);
    });
    
    if (state.compareEntities.length === 0) {
        document.getElementById('comparePapersChart').innerHTML = '<p style="text-align: center; color: var(--text-muted); padding: 4rem;">Search and add entities above to begin comparing.</p>';
        document.getElementById('compareAuthorsChart').innerHTML = '';
        return;
    }
    
    showSpinner('comparePapersChart');
    
    try {
        const promises = state.compareEntities.map(ent => 
            fetch(`/api/${ent.type}/${ent.id}/profile`).then(r => r.json())
        );
        const results = await Promise.all(promises);
        
        const multiSeriesData = results.map((res, i) => {
            return {
                name: state.compareEntities[i].title,
                color: state.compareEntities[i].color,
                values: res.yearly_stats || []
            };
        });
        
        d3.select('#comparePapersChart').selectAll("*").remove();
        d3.select('#compareAuthorsChart').selectAll("*").remove();
        
        if (window.drawMultiLineChart) {
            window.drawMultiLineChart('#comparePapersChart', multiSeriesData, 'year', 'paper_count');
            window.drawMultiLineChart('#compareAuthorsChart', multiSeriesData, 'year', 'distinct_authors');
        }
    } catch(err) {
        hideSpinner('comparePapersChart');
        console.error("Comparison Error", err);
    }
}

// ==================== TRENDS PAGE ====================
// Maximum categories to show in dropdown
const MAX_CATEGORIES_DISPLAY = 27;

function initTrendsPage() {
 // Initialize category trends page
 const entityTypeSel = document.getElementById('entityTypeSel');
 const categorySel = document.getElementById('categorySel');
 const addBtn = document.getElementById('addCategoryBtn');
 const clearBtn = document.getElementById('clearBtn');

 // State for selected categories
 state.categoryTrends = [];

 // Load categories based on entity type
 if (entityTypeSel) {
  entityTypeSel.addEventListener('change', () => {
   // Clear state when entity type changes to avoid mixing conference/journal categories
   state.categoryTrends = [];
   updateCategoryBadges();
  // Restore empty state messages
  document.getElementById('categoryTrendsChart').innerHTML = '<p class="chart-empty-state">Select a category above to view trends.</p>';
  document.getElementById('categoryPapersChart').innerHTML = '<p class="chart-empty-state">Select a category above to view publication trends.</p>';
 const spinner = document.getElementById('chartSpinner');
 if (spinner) spinner.style.display = 'flex';
   loadCategoryOptions(entityTypeSel.value);
  });
  // Initial load
  loadCategoryOptions('conference');
 }

 // Add category button
 if (addBtn) {
  addBtn.addEventListener('click', () => {
   const categoryCode = categorySel.value;
   const categoryName = categorySel.options[categorySel.selectedIndex].text;
   if (categoryCode && !state.categoryTrends.find(c => c.code === categoryCode)) {
    state.categoryTrends.push({ code: categoryCode, name: categoryName });
    updateCategoryBadges();
    loadCategoryChartData();
   }
  });
 }

 // Clear button
 if (clearBtn) {
  clearBtn.addEventListener('click', () => {
  state.categoryTrends = [];
  updateCategoryBadges();
  // Restore empty state messages
  document.getElementById('categoryTrendsChart').innerHTML = '<p class="chart-empty-state">Select a category above to view trends.</p>';
  document.getElementById('categoryPapersChart').innerHTML = '<p class="chart-empty-state">Select a category above to view publication trends.</p>';
  document.getElementById('chartSpinner').style.display = 'none';
 });
 }
}

async function loadCategoryOptions(entityType) {
 const categorySel = document.getElementById('categorySel');
 if (!categorySel) return;

 try {
  const res = await fetch(`/api/charts/category/${entityType}?list_only=true`);
  const data = await res.json();

  // Check for API errors
  if (data.error) {
   console.error('API error:', data.error);
   categorySel.innerHTML = '<option value="">Error: ' + data.error + '</option>';
   return;
  }

  categorySel.innerHTML = '';
  if (data.categories && data.categories.length > 0) {
   // Show categories up to MAX_CATEGORIES_DISPLAY
   data.categories.slice(0, MAX_CATEGORIES_DISPLAY).forEach(cat => {
    const option = document.createElement('option');
    // Ensure code is string for consistent comparison
    option.value = String(cat.code);
    option.textContent = cat.description || cat.code;
    categorySel.appendChild(option);
   });
  } else {
   categorySel.innerHTML = '<option value="">No categories found</option>';
  }
 } catch (err) {
  console.error('Failed to load categories:', err);
  categorySel.innerHTML = '<option value="">Error loading</option>';
 }
}

function updateCategoryBadges() {
 const container = document.getElementById('selectedCategories');
 if (!container) return;

 container.innerHTML = '';
 state.categoryTrends.forEach((cat, i) => {
  const badge = document.createElement('span');
  badge.className = 'badge category-badge';
  badge.innerHTML = `${cat.name} <span class="badge-remove-icon">&#10005;</span>`;
  badge.querySelector('span').onclick = () => {
   state.categoryTrends.splice(i, 1);
   updateCategoryBadges();
   loadCategoryChartData();
  };
  container.appendChild(badge);
 });
}

async function loadCategoryChartData() {
 // Clear charts if no categories selected
 if (state.categoryTrends.length === 0) {
  d3.select('#categoryTrendsChart').selectAll('*').remove();
  d3.select('#categoryPapersChart').selectAll('*').remove();
  // Restore empty state messages
  document.getElementById('categoryTrendsChart').innerHTML = '<p class="chart-empty-state">Select a category above to view trends.</p>';
  document.getElementById('categoryPapersChart').innerHTML = '<p class="chart-empty-state">Select a category above to view publication trends.</p>';
  return;
 }

 const entityType = document.getElementById('entityTypeSel').value;
 const spinner = document.getElementById('chartSpinner');
 if (spinner) spinner.style.display = 'flex';

 // PERFORMANCE: Fetch selectively to avoid heavy aggregations for all categories
 try {
  const selectedCodes = state.categoryTrends.map(c => c.code).join(',');
  const queryParam = entityType === 'conference' ? 'for_codes' : 'area_ids';
  const res = await fetch(`/api/charts/category/${entityType}?${queryParam}=${selectedCodes}`);
  const data = await res.json();
  
  // Color palette for multiple lines
  const colorPalette = ['#2563eb', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#06b6d4', '#84cc16'];
  
  // Filter and transform the data - find matching categories
  const multiSeriesData = [];
  state.categoryTrends.forEach((cat, i) => {
   // Find this category in the response
   const catData = data.categories?.find(c => String(c.code) === String(cat.code));
   if (catData && catData.yearly_data) {
    multiSeriesData.push({
     name: cat.name,
     color: colorPalette[i % colorPalette.length],
     values: catData.yearly_data.map(y => ({
      year: y.year,
      count: y.venue_count || 0,
      papers: y.paper_count || 0
     }))
    });
   }
  });

  // Render charts
  d3.select('#categoryTrendsChart').selectAll('*').remove();
  d3.select('#categoryPapersChart').selectAll('*').remove();
  if (spinner) spinner.style.display = 'none';

  if (multiSeriesData.length === 0) {
   document.getElementById('categoryTrendsChart').innerHTML = '<p class="chart-no-data">No data available for selected categories. Check console for details.</p>';
   return;
  }

  if (window.drawMultiLineChart) {
   // Venue count chart
   window.drawMultiLineChart('#categoryTrendsChart', multiSeriesData, 'year', 'count');
   // Papers chart
   window.drawMultiLineChart('#categoryPapersChart', multiSeriesData, 'year', 'papers');
  }
 } catch (err) {
  console.error('Failed to load category chart data:', err);
  if (spinner) spinner.style.display = 'none';
 }
}
