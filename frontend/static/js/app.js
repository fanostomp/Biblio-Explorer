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
    selectedConf: null,
    selectedJournal: null,
    selectedAuthor: null,
    compareEntities: [], // stores {type, id, title, color} for line chart comparison
    scatterData: null, // stores {scatter: [...]} for the scatter plot
    conferenceYearlyStats: [],
    journalYearlyStats: [],
    search: {
        page: 1,
        query: '',
        rank: '',
        category: '',
        quartile: '',
        area: '',
        publisher: ''
    }
};

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    const path = window.location.pathname;

    initMobileNav();

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
        loadDashboardStats();
    }
});

// --- Dashboard Animated Counter ---
function animateValue(obj, start, end, duration) {
    let startTimestamp = null;
    const formatNumber = (num) => {
        if (num >= 1000000) return (num / 1000000).toFixed(2) + 'M';
        if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
        return num.toLocaleString();
    };

    const step = (timestamp) => {
        if (!startTimestamp) startTimestamp = timestamp;
        const progress = Math.min((timestamp - startTimestamp) / duration, 1);
        // easeOutQuart
        const easeProgress = 1 - Math.pow(1 - progress, 4);
        const currentNum = Math.floor(easeProgress * (end - start) + start);
        obj.textContent = formatNumber(currentNum);
        if (progress < 1) {
            window.requestAnimationFrame(step);
        } else {
            obj.textContent = formatNumber(end);
        }
    };
    window.requestAnimationFrame(step);
}

async function loadDashboardStats() {
    try {
        const res = await fetch('/api/stats/overview');
        if (!res.ok) throw new Error(`Stats fetch failed with status ${res.status}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        const stats = [
            { id: 'totalPapersCount', value: data.total_papers },
            { id: 'totalAuthorsCount', value: data.total_authors },
            { id: 'totalVenuesCount', value: (data.total_conferences || 0) + (data.total_journals || 0) }
        ];

        stats.forEach(stat => {
            const el = document.getElementById(stat.id);
            if (el && stat.value) {
                animateValue(el, 0, stat.value, 2000);
            }
        });
    } catch (err) {
        console.error('Failed to load dashboard stats:', err);
    }
}

function debounce(fn, delay = 250) {
    let timeoutId;
    return (...args) => {
        window.clearTimeout(timeoutId);
        timeoutId = window.setTimeout(() => fn(...args), delay);
    };
}

function initMobileNav() {
    const navRoot = document.querySelector('[data-nav-root]');
    const navMenu = document.querySelector('[data-nav-menu]');
    const navToggle = document.getElementById('navMenuToggle');
    if (!navRoot || !navMenu || !navToggle) return;

    const setNavState = (isOpen) => {
        navRoot.classList.toggle('nav-open', isOpen);
        navToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    };

    navToggle.addEventListener('click', () => {
        const nextState = !navRoot.classList.contains('nav-open');
        setNavState(nextState);
    });

    navMenu.querySelectorAll('a').forEach((link) => {
        link.addEventListener('click', () => {
            if (window.innerWidth <= 768) {
                setNavState(false);
            }
        });
    });

    window.addEventListener('resize', debounce(() => {
        if (window.innerWidth > 768) {
            setNavState(false);
        }
    }, 100));
}

function parseYearInput(value) {
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) ? parsed : null;
}

function getYearFilterRange() {
    const startInput = document.getElementById('startYear');
    const endInput = document.getElementById('endYear');

    return {
        startYear: parseYearInput(startInput ? startInput.value : ''),
        endYear: parseYearInput(endInput ? endInput.value : '')
    };
}

function clearYearFilterInputs() {
    const startInput = document.getElementById('startYear');
    const endInput = document.getElementById('endYear');

    if (startInput) startInput.value = '';
    if (endInput) endInput.value = '';
}

function filterYearlyStatsByRange(yearlyStats, startYear, endYear) {
    if (!Array.isArray(yearlyStats)) return [];

    return yearlyStats.filter((row) => {
        const year = parseYearInput(row.year);
        if (year === null) return false;
        if (startYear !== null && year < startYear) return false;
        if (endYear !== null && year > endYear) return false;
        return true;
    });
}

function renderFilteredConfJournalCharts(yearlyStats) {
    ['papersChart', 'authorsChart'].forEach((id) => {
        const container = document.getElementById(id);
        if (!container) return;

        if (window.d3) {
            window.d3.select(container).selectAll('*').remove();
        } else {
            container.innerHTML = '';
        }
    });

    if (Array.isArray(yearlyStats) && yearlyStats.length > 0 && window.renderConfJournalCharts) {
        window.renderConfJournalCharts(yearlyStats);
    }
}

function setupProfileYearFilterButtons(config) {
    const applyFiltersBtn = document.getElementById('applyFilters');
    if (applyFiltersBtn) {
        applyFiltersBtn.addEventListener('click', () => {
            const selectedEntityId = state[config.selectedEntityStateKey];
            if (!selectedEntityId) return;

            const { startYear, endYear } = getYearFilterRange();
            renderFilteredConfJournalCharts(
                filterYearlyStatsByRange(state[config.yearlyStatsStateKey], startYear, endYear)
            );
            config.loadPapersFn(selectedEntityId);
        });
    }

    const resetFiltersBtn = document.getElementById('resetFilters');
    if (resetFiltersBtn) {
        resetFiltersBtn.addEventListener('click', () => {
            const selectedEntityId = state[config.selectedEntityStateKey];
            if (!selectedEntityId) return;

            clearYearFilterInputs();
            renderFilteredConfJournalCharts(state[config.yearlyStatsStateKey]);
            config.loadPapersFn(selectedEntityId);
        });
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
    loadSearchLookups('conference');

    const searchBtn = document.getElementById('searchBtn');
    const input = document.getElementById('conferenceSearch');
    const rankSel = document.getElementById('filterRank');
    const catSel = document.getElementById('filterCategory');

    const handleSearch = () => {
        state.search.page = 1;
        performPaginatedSearch('conference');
    };

    if (searchBtn) searchBtn.addEventListener('click', handleSearch);
    if (input) input.addEventListener('keyup', (e) => { if (e.key === 'Enter') handleSearch(); });
    if (rankSel) rankSel.addEventListener('change', handleSearch);
    if (catSel) catSel.addEventListener('change', handleSearch);

    const clearBtn = document.getElementById('clearFiltersBtn');
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            input.value = '';
            rankSel.value = '';
            catSel.value = '';
            state.search.page = 1;
            document.getElementById('searchResults').style.display = 'none';
        });
    }

    const backBtn = document.getElementById('backToSearch');
    if (backBtn) {
        backBtn.addEventListener('click', () => {
            document.getElementById('conferenceDetails').style.display = 'none';
            document.getElementById('filtersSection').style.display = 'none';
            document.getElementById('dashboardGrid').style.display = 'none';
            document.getElementById('searchResults').style.display = 'block';
        });
    }

    const prevBtn = document.getElementById('prevSearchPage');
    const nextBtn = document.getElementById('nextSearchPage');
    if (prevBtn) {
        prevBtn.addEventListener('click', () => {
            if (state.search.page > 1) {
                state.search.page--;
                performPaginatedSearch('conference');
            }
        });
    }
    if (nextBtn) {
        nextBtn.addEventListener('click', () => {
            state.search.page++;
            performPaginatedSearch('conference');
        });
    }

    setupAutocomplete({
        inputId: 'conferenceSearch',
        dropdownId: 'conferenceDropdown',
        dataSource: async (q) => {
            let url = `/api/conference/search?q=${encodeURIComponent(q)}&per_page=5`;
            const rank = document.getElementById('filterRank').value;
            const cat = document.getElementById('filterCategory').value;
            if (rank) url += `&rank=${encodeURIComponent(rank)}`;
            if (cat) url += `&category=${encodeURIComponent(cat)}`;

            const res = await fetch(url);
            if (!res.ok) throw new Error(`Autocomplete fetch failed with status ${res.status}`);
            const data = await res.json();
            return data.results || [];
        },
        displayFn: (match) => match.acronym ? `${match.acronym} - ${match.title}` : match.title,
        onSelect: (match) => loadConferenceProfile(match.conf_id)
    });

    setupProfileYearFilterButtons({
        selectedEntityStateKey: 'selectedConf',
        yearlyStatsStateKey: 'conferenceYearlyStats',
        loadPapersFn: loadConferencePapers
    });
}

async function loadSearchLookups(type) {
    try {
        const res = await fetch(`/api/${type}/lookups`);
        if (!res.ok) throw new Error(`Lookups fetch failed with status ${res.status}`);
        const data = await res.json();
        if (type === 'conference') {
            const rankSel = document.getElementById('filterRank');
            const catSel = document.getElementById('filterCategory');
            if (rankSel) data.ranks.forEach(r => rankSel.add(new Option(r.name, r.id)));
            if (catSel) data.categories.forEach(c => catSel.add(new Option(c.name, c.id)));
        } else {
            const qSel = document.getElementById('filterQuartile');
            const aSel = document.getElementById('filterArea');
            if (qSel) data.quartiles.forEach(q => qSel.add(new Option(q.name, q.id)));
            if (aSel) data.subject_areas.forEach(a => aSel.add(new Option(a.name, a.id)));
        }
    } catch (err) {
        console.error(`Failed to load ${type} lookups:`, err);
    }
}

async function performPaginatedSearch(type) {
    const q = document.getElementById(`${type}Search`).value;
    const commonParams = `q=${encodeURIComponent(q)}&page=${state.search.page}&per_page=10`;
    let url = `/api/${type}/search?${commonParams}`;

    if (type === 'conference') {
        const rank = document.getElementById('filterRank').value;
        const cat = document.getElementById('filterCategory').value;
        if (rank) url += `&rank=${encodeURIComponent(rank)}`;
        if (cat) url += `&category=${encodeURIComponent(cat)}`;
    } else {
        const qtl = document.getElementById('filterQuartile').value;
        const area = document.getElementById('filterArea').value;
        const pub = document.getElementById('filterPublisher').value;
        if (qtl) url += `&quartile=${encodeURIComponent(qtl)}`;
        if (area) url += `&subject_area=${encodeURIComponent(area)}`;
        if (pub) url += `&publisher=${encodeURIComponent(pub)}`;
        if (isJournalCoverageFilterEnabled()) url += '&with_dblp_coverage=true';
    }

    const resultsContainer = document.getElementById('searchResults');
    showSpinner('searchResults');
    resultsContainer.style.display = 'block';
    
    // Hide profile if showing
    const details = document.getElementById(`${type}Details`);
    if (details) details.style.display = 'none';
    const grid = document.getElementById('dashboardGrid');
    if (grid) grid.style.display = 'none';
    const filters = document.getElementById('filtersSection');
    if (filters) filters.style.display = 'none';
    if (type === 'journal') {
        const noCoverage = document.getElementById('journalNoCoverage');
        if (noCoverage) noCoverage.style.display = 'none';
    }

    try {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`Fetch failed with status ${res.status}`);
        const data = await res.json();
        renderSearchResults(type, data);
    } catch (err) {
        console.error('Search failed:', err);
    } finally {
        hideSpinner('searchResults');
    }
}

function renderSearchResults(type, data) {
    const tbody = document.querySelector('#resultsTable tbody');
    const countEl = document.getElementById('resultsCount');
    const pageInfo = document.getElementById('searchPageInfo');
    const prevBtn = document.getElementById('prevSearchPage');
    const nextBtn = document.getElementById('nextSearchPage');

    tbody.innerHTML = '';
    const results = data.results || [];
    const pag = data.pagination || {};

    countEl.textContent = `Found ${pag.total_records || 0} items`;
    pageInfo.textContent = `Page ${pag.page || 1} of ${pag.total_pages || 1}`;
    prevBtn.disabled = pag.page <= 1;
    nextBtn.disabled = pag.page >= pag.total_pages;

    results.forEach(item => {
        const tr = document.createElement('tr');
        if (type === 'conference') {
            tr.innerHTML = `
                <td><b>${escapeHtml(item.acronym)}</b></td>
                <td>${escapeHtml(item.title)}</td>
                <td><span class="badge rank-badge">${escapeHtml(item.rank)}</span></td>
                <td class="action-cell"></td>
            `;
            const btn = document.createElement('button');
            btn.className = 'btn secondary-btn small';
            btn.textContent = 'View Profile';
            btn.addEventListener('click', () => loadConferenceProfile(item.conf_id));
            tr.querySelector('.action-cell').appendChild(btn);
        } else {
            const coverageCell = document.createElement('td');
            coverageCell.appendChild(createCoverageBadge(Boolean(item.has_dblp_coverage), 'search'));
            tr.innerHTML = `
                <td><b>${escapeHtml(item.title)}</b></td>
                <td>${escapeHtml(item.publisher)}</td>
                <td><span class="badge rank-badge" style="background: ${getQuartileColor(item.best_quartile)}">${escapeHtml(item.best_quartile)}</span></td>
                <td class="coverage-cell"></td>
                <td>${item.sjr_index ?? '0.0'}</td>
                <td class="action-cell"></td>
            `;
            tr.querySelector('.coverage-cell').appendChild(coverageCell.firstChild);
            const btn = document.createElement('button');
            btn.className = 'btn secondary-btn small';
            btn.textContent = 'View Profile';
            btn.addEventListener('click', () => loadJournalProfile(item.journal_id));
            tr.querySelector('.action-cell').appendChild(btn);
        }
        tbody.appendChild(tr);
    });
}

function getQuartileColor(q) {
    if (q === 'Q1') return '#10b981';
    if (q === 'Q2') return '#fde047';
    if (q === 'Q3') return '#f97316';
    if (q === 'Q4') return '#ef4444';
    return '#6b7280';
}

function isJournalCoverageFilterEnabled() {
    const checkbox = document.getElementById('filterCoverageOnly');
    return Boolean(checkbox && checkbox.checked);
}

function createCoverageBadge(hasCoverage, variant = 'search') {
    const badge = document.createElement('span');
    badge.className = 'badge coverage-badge';

    if (hasCoverage) {
        badge.textContent = variant === 'profile' ? 'DBLP coverage available' : 'DBLP stats';
        badge.classList.add('coverage-badge-covered');
        badge.title = 'DBLP-linked paper statistics are available for this journal.';
    } else {
        badge.textContent = 'Ranked only';
        badge.classList.add('coverage-badge-ranked-only');
        badge.title = 'Ranking metadata is available, but DBLP-linked paper statistics are not.';
    }

    if (variant === 'search') {
        badge.classList.add('coverage-badge-search');
    }

    return badge;
}

function renderTopAuthorsTable(authors) {
    const tbody = document.querySelector('#topAuthorsTable tbody');
    if (!tbody) return;

    tbody.innerHTML = '';

    if (!Array.isArray(authors) || authors.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding: 2rem; color: var(--text-muted);">No authors found</td></tr>';
        return;
    }

    authors.forEach((author, index) => {
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
        tdAction.innerHTML = '<span style="font-size: 0.85rem; color: var(--text-muted);">Search in Authors tab</span>';

        tr.append(tdRank, tdName, tdCount, tdAction);
        tbody.appendChild(tr);
    });
}

function setupJournalLiveSearch(input, onSearch) {
    if (!input || typeof onSearch !== 'function') return;

    const debouncedSearch = debounce(() => {
        onSearch();
    }, 250);

    input.addEventListener('input', () => {
        debouncedSearch();
    });
}

async function loadConferenceProfile(id) {
    document.getElementById('searchResults').style.display = 'none';
    state.selectedConf = id;
    showSpinner('dashboardGrid');
    try {
        const res = await fetch(`/api/conference/${id}/profile`);
        if (!res.ok) throw new Error(`Profile fetch failed with status ${res.status}`);
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
        clearYearFilterInputs();
        state.conferenceYearlyStats = Array.isArray(data.yearly_stats) ? data.yearly_stats : [];

        // Populate Stat Cards
        document.getElementById('statTotalPapers').textContent = p.total_papers || 0;
        document.getElementById('statTotalAuthors').textContent = p.distinct_authors || 0;
        document.getElementById('statYears').textContent = p.first_year ? `${p.first_year} - ${p.last_year}` : '-';
        document.getElementById('statAvgAuthors').textContent = p.avg_authors_per_paper || 0;

        hideSpinner('dashboardGrid');

        // Plot Charts
        renderFilteredConfJournalCharts(state.conferenceYearlyStats);

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
        if (!res.ok) throw new Error(`Papers fetch failed with status ${res.status}`);
        if (!res.ok) throw new Error(`Fetch failed with status ${res.status}`);
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
                const dblpUrl = p.url.startsWith('http://') || p.url.startsWith('https://')
                    ? p.url
                    : `https://dblp.org/${p.url.replace(/^\/+/, '')}`;

                dblpLink.href = dblpUrl;
                dblpLink.target = '_blank';
                dblpLink.rel = 'noopener noreferrer';
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
        if (!res.ok) throw new Error(`Top authors fetch failed with status ${res.status}`);
        const data = await res.json();
        renderTopAuthorsTable(data);
    } catch(err) {
        console.error(err);
    }
}

// ==========================================
// JOURNAL PAGE LOGIC
// ==========================================
function initJournalPage() {
    loadSearchLookups('journal');

    const searchBtn = document.getElementById('searchBtn');
    const input = document.getElementById('journalSearch');
    const pubInput = document.getElementById('filterPublisher');
    const qtlSel = document.getElementById('filterQuartile');
    const areaSel = document.getElementById('filterArea');
    const coverageOnly = document.getElementById('filterCoverageOnly');

    const handleSearch = () => {
        state.search.page = 1;
        performPaginatedSearch('journal');
    };

    if (searchBtn) searchBtn.addEventListener('click', handleSearch);
    if (input) input.addEventListener('keyup', (e) => { if (e.key === 'Enter') handleSearch(); });
    setupJournalLiveSearch(input, handleSearch);
    setupJournalLiveSearch(pubInput, handleSearch);
    if (qtlSel) qtlSel.addEventListener('change', handleSearch);
    if (areaSel) areaSel.addEventListener('change', handleSearch);
    if (coverageOnly) coverageOnly.addEventListener('change', handleSearch);

    const clearBtn = document.getElementById('clearFiltersBtn');
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            input.value = '';
            qtlSel.value = '';
            areaSel.value = '';
            if (pubInput) pubInput.value = '';
            if (coverageOnly) coverageOnly.checked = false;
            state.search.page = 1;
            document.getElementById('searchResults').style.display = 'none';
        });
    }

    const backBtn = document.getElementById('backToSearch');
    if (backBtn) {
        backBtn.addEventListener('click', () => {
            document.getElementById('journalDetails').style.display = 'none';
            document.getElementById('filtersSection').style.display = 'none';
            document.getElementById('dashboardGrid').style.display = 'none';
            const noCoverage = document.getElementById('journalNoCoverage');
            if (noCoverage) noCoverage.style.display = 'none';
            document.getElementById('searchResults').style.display = 'block';
        });
    }

    const prevBtn = document.getElementById('prevSearchPage');
    const nextBtn = document.getElementById('nextSearchPage');
    if (prevBtn) {
        prevBtn.addEventListener('click', () => {
            if (state.search.page > 1) {
                state.search.page--;
                performPaginatedSearch('journal');
            }
        });
    }
    if (nextBtn) {
        nextBtn.addEventListener('click', () => {
            state.search.page++;
            performPaginatedSearch('journal');
        });
    }

    setupAutocomplete({
        inputId: 'journalSearch',
        dropdownId: 'journalDropdown',
        dataSource: async (q) => {
            let url = `/api/journal/search?q=${encodeURIComponent(q)}&per_page=5`;
            const qtl = document.getElementById('filterQuartile').value;
            const area = document.getElementById('filterArea').value;
            const pub = document.getElementById('filterPublisher').value;
            if (qtl) url += `&quartile=${encodeURIComponent(qtl)}`;
            if (area) url += `&subject_area=${encodeURIComponent(area)}`;
            if (pub) url += `&publisher=${encodeURIComponent(pub)}`;
            if (isJournalCoverageFilterEnabled()) url += '&with_dblp_coverage=true';

            const res = await fetch(url);
            if (!res.ok) throw new Error(`Autocomplete fetch failed with status ${res.status}`);
            const data = await res.json();
            return data.results || [];
        },
        displayFn: (match) => match.title,
        onSelect: (match) => loadJournalProfile(match.journal_id)
    });

    setupProfileYearFilterButtons({
        selectedEntityStateKey: 'selectedJournal',
        yearlyStatsStateKey: 'journalYearlyStats',
        loadPapersFn: loadJournalPapers
    });
}

async function loadJournalProfile(id) {
    document.getElementById('searchResults').style.display = 'none';
    state.selectedJournal = id;
    showSpinner('dashboardGrid');
    try {
        const res = await fetch(`/api/journal/${id}/profile`);
        if (!res.ok) throw new Error(`Profile fetch failed with status ${res.status}`);
        const data = await res.json();
        
        if (data.error) return alert("Profile not found.");

        const p = data.profile;
        document.getElementById('journalTitle').textContent = p.title || 'Unknown Title';
        const rankEl = document.getElementById('journalRank');
        const coverageBadge = document.getElementById('journalCoverageBadge');
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
        const hasCoverage = Boolean(data.has_dblp_coverage);
        const filtersSection = document.getElementById('filtersSection');
        const dashboardGrid = document.getElementById('dashboardGrid');
        const noCoverage = document.getElementById('journalNoCoverage');
        if (coverageBadge) {
            const updatedCoverageBadge = createCoverageBadge(hasCoverage, 'profile');
            updatedCoverageBadge.id = 'journalCoverageBadge';
            coverageBadge.replaceWith(updatedCoverageBadge);
        }
        clearYearFilterInputs();
        state.journalYearlyStats = Array.isArray(data.yearly_stats) ? data.yearly_stats : [];

        filtersSection.style.display = hasCoverage ? 'block' : 'none';
        dashboardGrid.style.display = hasCoverage ? 'flex' : 'none';
        if (noCoverage) noCoverage.style.display = hasCoverage ? 'none' : 'block';

        hideSpinner('dashboardGrid');

        if (hasCoverage) {
            renderFilteredConfJournalCharts(state.journalYearlyStats);
        }

        if (hasCoverage) {
            loadJournalPapers(id);
            loadJournalTopAuthors(id);
        } else {
            const papersChart = document.getElementById('papersChart');
            const authorsChart = document.getElementById('authorsChart');
            const tbody = document.querySelector('#papersTable tbody');
            renderTopAuthorsTable([]);
            if (papersChart) papersChart.innerHTML = '';
            if (authorsChart) authorsChart.innerHTML = '';
            if (tbody) tbody.innerHTML = '';
        }

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
        if (!res.ok) throw new Error(`Papers fetch failed with status ${res.status}`);
        if (!res.ok) throw new Error(`Fetch failed with status ${res.status}`);
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

async function loadJournalTopAuthors(id) {
    try {
        const res = await fetch(`/api/journal/${id}/top_authors?limit=10`);
        if (!res.ok) throw new Error(`Top authors fetch failed with status ${res.status}`);
        const data = await res.json();
        renderTopAuthorsTable(data);
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
                if (!res.ok) throw new Error(`Author search failed with status ${res.status}`);
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
        if (!res.ok) throw new Error(`Author profile failed with status ${res.status}`);
        if (!res.ok) throw new Error(`Fetch failed with status ${res.status}`);
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
        if (!pres.ok) throw new Error(`Author papers failed with status ${pres.status}`);
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
        if (!res.ok) throw new Error(`Year profile failed with status ${res.status}`);
        if (!res.ok) throw new Error(`Fetch failed with status ${res.status}`);
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
        const pres = await fetch(`/api/year/${year}/papers?page=1&per_page=250`);
        if (!pres.ok) throw new Error(`Year papers failed with status ${pres.status}`);
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
        if (!res.ok) throw new Error(`Charts overview failed with status ${res.status}`);
        if (!res.ok) throw new Error(`Fetch failed with status ${res.status}`);
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
        if (!res.ok) throw new Error(`Publisher chart failed with status ${res.status}`);
        if (!res.ok) throw new Error(`Fetch failed with status ${res.status}`);
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
            if (!res.ok) throw new Error(`Scatter metrics failed with status ${res.status}`);
            if (!res.ok) throw new Error(`Fetch failed with status ${res.status}`);
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

function setAutocompleteStatus(dropdown, message, extraClass = '') {
    dropdown.innerHTML = '';
    const li = document.createElement('li');
    li.className = `autocomplete-status ${extraClass}`.trim();
    li.textContent = message;
    li.setAttribute('aria-disabled', 'true');
    dropdown.appendChild(li);
    dropdown.style.display = 'block';
}

function renderComparisonMatches(dropdown, input, type, matches) {
    dropdown.innerHTML = '';
    dropdown.style.display = 'block';

    matches.forEach((match) => {
        const isConference = type === 'conference';
        const titleStr = isConference && match.acronym ? `[${match.acronym}] ${match.title}` : match.title;
        const id = isConference ? match.conf_id : match.journal_id;
        const li = document.createElement('li');

        li.textContent = titleStr;
        li.onclick = () => {
            input.value = '';
            dropdown.style.display = 'none';
            addEntityToComparison(type, id, titleStr);
        };
        dropdown.appendChild(li);
    });
}

async function fetchComparisonMatches(type, query, signal) {
    const endpoint = type === 'conference'
        ? `/api/conference/search?q=${encodeURIComponent(query)}`
        : `/api/journal/search?q=${encodeURIComponent(query)}`;
    const res = await fetch(endpoint, { signal });
    if (!res.ok) {
        throw new Error(`Search failed with status ${res.status}`);
    }
    if (!res.ok) throw new Error(`Fetch failed with status ${res.status}`);
   const data = await res.json();
    const results = data.results || [];
    return results.slice(0, 10);
}

function setupComparisonAutocomplete() {
    const input = document.getElementById('addEntitySearch');
    const dropdown = document.getElementById('addEntityDropdown');
    const typeSel = document.getElementById('entityTypeSel');
    const controls = document.getElementById('comparisonControls');
    if(!input || !dropdown || !typeSel || !controls) return;

    let activeController = null;
    let requestToken = 0;

    const updatePlaceholder = () => {
        input.placeholder = typeSel.value === 'conference'
            ? 'Search conferences to add...'
            : 'Search journals to add...';
    };

    const handleSearchInput = debounce(async () => {
        const query = input.value.trim();
        const type = typeSel.value;
        requestToken += 1;
        const currentToken = requestToken;

        if (activeController) {
            activeController.abort();
            activeController = null;
        }

        if (query.length < 2) {
            dropdown.innerHTML = '';
            dropdown.style.display = 'none';
            return;
        }

        activeController = new AbortController();
        setAutocompleteStatus(dropdown, 'Searching...');

        try {
            const matches = await fetchComparisonMatches(type, query, activeController.signal);
            if (currentToken !== requestToken) return;

            if (matches.length === 0) {
                setAutocompleteStatus(dropdown, `No matching ${type}s found.`);
                return;
            }

            renderComparisonMatches(dropdown, input, type, matches);
        } catch (err) {
            if (err.name === 'AbortError') return;
            console.error(`Failed to search ${type}s:`, err);
            if (currentToken !== requestToken) return;
            setAutocompleteStatus(dropdown, 'Search unavailable. Try again.', 'autocomplete-status-error');
        }
    }, 250);

    updatePlaceholder();

    input.addEventListener('input', handleSearchInput);
    typeSel.addEventListener('change', () => {
        requestToken += 1;
        if (activeController) {
            activeController.abort();
            activeController = null;
        }
        input.value = '';
        dropdown.innerHTML = '';
        dropdown.style.display = 'none';
        updatePlaceholder();
    });

    document.addEventListener('click', (e) => {
        if (!controls.contains(e.target)) {
            requestToken += 1;
            if (activeController) {
                activeController.abort();
                activeController = null;
            }
            dropdown.style.display = 'none';
        }
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
    const warnings = document.getElementById('comparisonWarnings');
    const papersChart = document.getElementById('comparePapersChart');
    const authorsChart = document.getElementById('compareAuthorsChart');
    list.innerHTML = '';
    if (warnings) warnings.innerHTML = '';
    
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
        removeBtn.addEventListener('click', () => removeEntityFromComparison(i));
        badge.appendChild(removeBtn);
        
        list.appendChild(badge);
    });
    
    if (state.compareEntities.length === 0) {
        papersChart.innerHTML = '<p class="chart-empty-state">Search and add entities above to begin comparing.</p>';
        authorsChart.innerHTML = '';
        return;
    }
    
    showSpinner('comparePapersChart');
    
    try {
        const promises = state.compareEntities.map(ent => 
            fetch(`/api/${ent.type}/${ent.id}/profile`).then(r => {
                if (!r.ok) throw new Error(`Comparison entity failed: ${r.status}`);
                return r.json();
            })
        );
        const results = await Promise.all(promises);
        
        const missingDataMessages = [];
        const multiSeriesData = [];

        results.forEach((res, i) => {
            const yearlyStats = Array.isArray(res.yearly_stats) ? res.yearly_stats : [];
            if (yearlyStats.length === 0) {
                missingDataMessages.push(`No comparison data available for ${state.compareEntities[i].title}.`);
                return;
            }

            multiSeriesData.push({
                name: state.compareEntities[i].title,
                color: state.compareEntities[i].color,
                values: yearlyStats
            });
        });
        
        d3.select('#comparePapersChart').selectAll("*").remove();
        d3.select('#compareAuthorsChart').selectAll("*").remove();

        if (warnings && missingDataMessages.length > 0) {
            missingDataMessages.forEach((message) => {
                const warning = document.createElement('p');
                warning.className = 'comparison-warning';
                warning.textContent = message;
                warnings.appendChild(warning);
            });
        }

        if (multiSeriesData.length === 0) {
            papersChart.innerHTML = '<p class="chart-empty-state">Selected venues do not have comparison data.</p>';
            authorsChart.innerHTML = '<p class="chart-empty-state">Selected venues do not have comparison data.</p>';
            return;
        }

        if (window.drawMultiLineChart) {
            window.drawMultiLineChart('#comparePapersChart', multiSeriesData, 'year', 'paper_count');
            window.drawMultiLineChart('#compareAuthorsChart', multiSeriesData, 'year', 'distinct_authors');
        }
    } catch(err) {
        hideSpinner('comparePapersChart');
        console.error("Comparison Error", err);
        if (warnings) {
            warnings.innerHTML = '<p class="comparison-warning">Unable to load comparison data right now. Please try again.</p>';
        }
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
  const removeIcon = badge.querySelector('span');
  removeIcon.addEventListener('click', () => {
   state.categoryTrends.splice(i, 1);
   updateCategoryBadges();
   loadCategoryChartData();
  });
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
