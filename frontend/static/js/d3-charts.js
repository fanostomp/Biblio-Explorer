// ==========================================
// D3.js Charts Module
// ==========================================

// Sample fallback data for homepage if API is unavailable
function renderSampleChart() {
    const data = [
        {year: 2018, papers: 140000},
        {year: 2019, papers: 165000},
        {year: 2020, papers: 180000},
        {year: 2021, papers: 210000},
        {year: 2022, papers: 230000},
        {year: 2023, papers: 250000},
        {year: 2024, papers: 245000},
        {year: 2025, papers: 260000}
    ];
    drawAnimatedLineChart('#chart', data, 'year', 'papers', "#38bdf8");
}

// Global scope for reusability on Profile Pages
window.renderConfJournalCharts = function(yearlyStats) {
    if (!yearlyStats || yearlyStats.length === 0) return;
    
    // Papers over time
    d3.select('#papersChart').selectAll("*").remove();
    drawAnimatedLineChart('#papersChart', yearlyStats, 'year', 'paper_count', "#2563eb");

    // Authors over time
    d3.select('#authorsChart').selectAll("*").remove();
    drawAnimatedLineChart('#authorsChart', yearlyStats, 'year', 'distinct_authors', "#38bdf8");
}

window.renderAuthorCharts = function(yearlyStats) {
    if (!yearlyStats || yearlyStats.length === 0) return;
    
    // Publication trend over time
    d3.select('#papersChart').selectAll("*").remove();
    
    // We can use drawMultiLineChart to show Conf vs Journal breakdown
    const multiSeries = [
        {
            name: 'Conferences',
            color: '#2563eb',
            values: yearlyStats.map(d => ({ year: d.year, amount: d.conf_count }))
        },
        {
            name: 'Journals',
            color: '#38bdf8',
            values: yearlyStats.map(d => ({ year: d.year, amount: d.journal_count }))
        }
    ];
    
    // If we only have one type of data, maybe just a simple line is better?
    // But drawMultiLineChart works fine for 2 series.
    window.drawMultiLineChart('#papersChart', multiSeries, 'year', 'amount');
}

// Fix #5: Reusable tooltip — create once, reuse everywhere
const chartTooltip = (function() {
    let tip = null;
    return function() {
        if (!tip) {
            tip = d3.select("body").append("div")
                .attr("class", "chart-tooltip")
                .attr("style", "position: absolute; opacity: 0; background: var(--surface-color); padding: 8px 12px; border-radius: 6px; pointer-events: none; border: 1px solid rgba(255,255,255,0.1); color: var(--text-color); font-size: 0.9rem; z-index: 1001; box-shadow: 0 4px 6px rgba(0,0,0,0.3);");
        }
        return tip;
    };
})();

/**
 * Reusable D3.js Animated Line Chart Renderer
 * Fix #12: Uses viewBox for responsive SVGs
 */
function drawAnimatedLineChart(containerSelector, data, xKey, yKey, colorStr) {
    const margin = {top: 20, right: 30, bottom: 40, left: 60};
    const container = document.querySelector(containerSelector);
    if (!container) return;
    
    const totalWidth = container.clientWidth || 600;
    const totalHeight = 500;
    const width = totalWidth - margin.left - margin.right;
    const height = totalHeight - margin.top - margin.bottom;

    const svg = d3.select(containerSelector)
        .append("svg")
        .attr("viewBox", `0 0 ${totalWidth} ${totalHeight}`)
        .attr("preserveAspectRatio", "xMidYMid meet")
        .attr("width", "100%")
        .append("g")
        .attr("transform", `translate(${margin.left},${margin.top})`);

    const x = d3.scaleLinear()
        .domain(d3.extent(data, d => d[xKey]))
        .range([0, width]);

    svg.append("g")
        .attr("transform", `translate(0,${height})`)
        .call(d3.axisBottom(x).tickFormat(d3.format("d")));

    const y = d3.scaleLinear()
        .domain([0, d3.max(data, d => d[yKey]) * 1.1])
        .range([height, 0]);

    svg.append("g")
        .call(d3.axisLeft(y).ticks(5));

    // Line generator
    const line = d3.line()
        .x(d => x(d[xKey]))
        .y(d => y(d[yKey]))
        .curve(d3.curveMonotoneX);

    // Add line path
    const path = svg.append("path")
        .datum(data)
        .attr("fill", "none")
        .attr("stroke", colorStr)
        .attr("stroke-width", 3)
        .attr("d", line);

    // Add animation
    const totalLength = path.node().getTotalLength();
    path
        .attr("stroke-dasharray", totalLength + " " + totalLength)
        .attr("stroke-dashoffset", totalLength)
        .transition()
        .duration(1500)
        .ease(d3.easeLinear)
        .attr("stroke-dashoffset", 0);

    // Fix #5: reuse shared tooltip
    const tooltip = chartTooltip();
        
    // Add dots
    svg.selectAll(".dot")
        .data(data)
        .enter().append("circle")
        .attr("class", "dot")
        .attr("cx", d => x(d[xKey]))
        .attr("cy", d => y(d[yKey]))
        .attr("r", 5)
        .attr("fill", "var(--background-color)")
        .attr("stroke", colorStr)
        .attr("stroke-width", 2)
        .style("opacity", 0)
        .on("mouseover", function(event, d) {
            d3.select(this).attr("r", 8).attr("fill", colorStr);
            tooltip.transition().duration(200).style("opacity", .9);
            tooltip.html(`<strong>${d[xKey]}</strong><br/>Count: ${d[yKey]}`)
                .style("left", (event.pageX + 10) + "px")
                .style("top", (event.pageY - 28) + "px");
        })
        .on("mouseout", function(d) {
            d3.select(this).attr("r", 5).attr("fill", "var(--background-color)");
            tooltip.transition().duration(500).style("opacity", 0);
        })
        .transition()
        .delay((d, i) => i * (1500 / data.length))
        .duration(500)
        .style("opacity", 1);
}

/**
 * Reusable D3.js Animated Multi-Line Chart Renderer (Targeted Comparison)
 * Fix #12: Uses viewBox for responsive SVGs
 */
window.drawMultiLineChart = function(containerSelector, multiSeriesData, xKey, yKey) {
    const margin = {top: 30, right: 120, bottom: 40, left: 60};
    const container = document.querySelector(containerSelector);
    if (!container || !multiSeriesData || multiSeriesData.length === 0) return;
    
    const totalWidth = container.clientWidth || 800;
    const totalHeight = 500;
    const width = totalWidth - margin.left - margin.right;
    const height = totalHeight - margin.top - margin.bottom;

    const svg = d3.select(containerSelector)
        .append("svg")
        .attr("viewBox", `0 0 ${totalWidth} ${totalHeight}`)
        .attr("preserveAspectRatio", "xMidYMid meet")
        .attr("width", "100%")
        .append("g")
        .attr("transform", `translate(${margin.left},${margin.top})`);

    // Flatten all data to find global extents
    const allValues = multiSeriesData.flatMap(s => s.values);
    if (allValues.length === 0) return;

    const x = d3.scaleLinear()
        .domain(d3.extent(allValues, d => d[xKey]))
        .range([0, width]);

    svg.append("g")
        .attr("transform", `translate(0,${height})`)
        .call(d3.axisBottom(x).tickFormat(d3.format("d")));

    const y = d3.scaleLinear()
        .domain([0, d3.max(allValues, d => d[yKey]) * 1.1])
        .range([height, 0]);

    svg.append("g")
        .call(d3.axisLeft(y).ticks(6));

    // Line generator
    const line = d3.line()
        .x(d => x(d[xKey]))
        .y(d => y(d[yKey]))
        .curve(d3.curveMonotoneX);

    // Create a group for each series
    const series = svg.selectAll(".series")
        .data(multiSeriesData)
        .enter().append("g")
        .attr("class", "series");

    // Add path
    const paths = series.append("path")
        .attr("fill", "none")
        .attr("stroke", d => d.color)
        .attr("stroke-width", 3)
        .attr("d", d => line(d.values));

    // Animate paths
    paths.each(function() {
        const totalLength = this.getTotalLength();
        d3.select(this)
            .attr("stroke-dasharray", totalLength + " " + totalLength)
            .attr("stroke-dashoffset", totalLength)
            .transition()
            .duration(1500)
            .ease(d3.easeLinear)
            .attr("stroke-dashoffset", 0);
    });

    // Fix #5: reuse shared tooltip
    const tooltip = chartTooltip();

    // Add points for each series
    series.selectAll(".dot")
        .data(d => d.values.map(v => ({...v, color: d.color, name: d.name})))
        .enter().append("circle")
        .attr("class", "dot")
        .attr("cx", d => x(d[xKey]))
        .attr("cy", d => y(d[yKey]))
        .attr("r", 5)
        .attr("fill", "var(--background-color)")
        .attr("stroke", d => d.color)
        .attr("stroke-width", 2)
        .style("opacity", 0)
        .on("mouseover", function(event, d) {
            d3.select(this).attr("r", 8).attr("fill", d.color);
            tooltip.transition().duration(200).style("opacity", .9);
            tooltip.html(`<strong>${d.name}</strong><br/>Year: ${d[xKey]}<br/>Count: ${d[yKey]}`)
                .style("left", (event.pageX + 10) + "px")
                .style("top", (event.pageY - 28) + "px");
        })
        .on("mouseout", function(d) {
            d3.select(this).attr("r", 5).attr("fill", "var(--background-color)");
            tooltip.transition().duration(500).style("opacity", 0);
        })
        .transition()
        .delay((d, i, nodes) => {
            const parentLen = d3.select(nodes[i].parentNode).datum().values.length;
            return i * (1500 / parentLen);
        })
        .duration(500)
        .style("opacity", 1);
};

/**
 * Reusable D3.js Bar Chart Renderer 
 * Supports simple bar charts and grouped bar charts for comparison.
 * Option parameters:
 *   - colors: Array of hex color strings mapped to yKeys.
 *   - grouped: Boolean ensuring side-by-side grouped bars for multiple keys.
 *   - legend: Boolean to draw legend based on yKeys.
 */
window.drawBarChart = function(containerSelector, data, xKey, yKeys, options = {}) {
    // Defaults
    const colors = options.colors || ["#2563eb", "#38bdf8", "#fde047", "#f43f5e"];
    const isGrouped = options.grouped || false;
    const showLegend = options.legend !== false;
    const formatLabel = options.labelFormatter || (key => key);

    const margin = {top: 40, right: 30, bottom: (xKey.includes('name') || xKey.includes('publisher')) ? 100 : 50, left: 60};
    const container = document.querySelector(containerSelector);
    if (!container || !data || data.length === 0) return;
    
    // Clear previous
    d3.select(containerSelector).selectAll("*").remove();

    const totalWidth = container.clientWidth || 800;
    const totalHeight = 500;
    const width = totalWidth - margin.left - margin.right;
    const height = totalHeight - margin.top - margin.bottom;

    const svg = d3.select(containerSelector)
        .append("svg")
        .attr("viewBox", `0 0 ${totalWidth} ${totalHeight}`)
        .attr("preserveAspectRatio", "xMidYMid meet")
        .attr("width", "100%")
        .append("g")
        .attr("transform", `translate(${margin.left},${margin.top})`);

    // Main X scale for the categories (e.g., publisher name)
    const x0 = d3.scaleBand()
        .domain(data.map(d => d[xKey]))
        .rangeRound([0, width])
        .paddingInner(0.2);

    // Sub X scale for grouped bars within each category
    const x1 = d3.scaleBand()
        .domain(yKeys)
        .rangeRound([0, x0.bandwidth()])
        .padding(0.05);

    // Draw X Axis
    const xAxis = svg.append("g")
        .attr("transform", `translate(0,${height})`)
        .call(d3.axisBottom(x0));
        
    // Slanted text if we expect long labels
    if (xKey.includes('name') || xKey.includes('publisher')) {
        xAxis.selectAll("text")
            .attr("y", 10)
            .attr("x", -5)
            .attr("dy", ".35em")
            .attr("transform", "rotate(-45)")
            .style("text-anchor", "end");
    }

    // Y Scale must start from 0
    let maxY = d3.max(data, d => {
        return d3.max(yKeys, key => Number(d[key]));
    });
    
    const y = d3.scaleLinear()
        .domain([0, (maxY || 0) * 1.1])
        .rangeRound([height, 0]);

    svg.append("g")
        .call(d3.axisLeft(y).ticks(6));

    // Color scale mapping keys to provided colors
    const colorScale = d3.scaleOrdinal()
        .domain(yKeys)
        .range(colors);

    const tooltip = chartTooltip();

    // Add Groups map to data
    const barGroups = svg.append("g")
        .selectAll("g")
        .data(data)
        .enter().append("g")
        .attr("transform", d => `translate(${x0(d[xKey])},0)`);

    // Determine scale and width based on whether it is grouped or simple
    const getX = isGrouped ? (key) => x1(key) : () => 0;
    const getWidth = isGrouped ? x1.bandwidth() : x0.bandwidth();

    // Map the actual nested rects
    yKeys.forEach((key, ind) => {
        barGroups.selectAll(`.rect-${key}`)
            .data(d => [d])
            .enter().append("rect")
            .attr("class", `rect-${key}`)
            .attr("x", () => getX(key))
            .attr("y", height) // Start from bottom for animation
            .attr("width", getWidth)
            .attr("height", 0) // Start with 0 height
            .attr("fill", colorScale(key))
            .attr("rx", 3) // Rounded corners at top
            .attr("ry", 3)
            .on("mouseover", function(event, d) {
                d3.select(this).attr("opacity", 0.8);
                tooltip.transition().duration(200).style("opacity", .9);
                tooltip.html(`<strong>${d[xKey]}</strong><br/>${formatLabel(key)}: ${d[key]}`)
                    .style("left", (event.pageX + 10) + "px")
                    .style("top", (event.pageY - 28) + "px");
            })
            .on("mouseout", function() {
                d3.select(this).attr("opacity", 1);
                tooltip.transition().duration(500).style("opacity", 0);
            })
            .transition()
            .duration(1000)
            .delay((d, i) => i * 50 + (ind * 100))
            .attr("y", d => y(d[key]))
            .attr("height", d => height - y(d[key]));
    });

    // Legend
    if (showLegend && yKeys.length > 1) {
        const legend = svg.append("g")
            .attr("class", "chart-legend")
            .attr("text-anchor", "end")
            .selectAll("g")
            .data(yKeys.slice().reverse())
            .enter().append("g")
            .attr("transform", (d, i) => `translate(0,${i * 20})`);

        legend.append("rect")
            .attr("x", width - 19)
            .attr("width", 19)
            .attr("height", 19)
            .attr("rx", 3)
            .attr("fill", colorScale);


        legend.append("text")
            .attr("x", width - 24)
            .attr("y", 9.5)
            .attr("dy", "0.32em")
            .attr("fill", "var(--text-color)")
            .text(d => formatLabel(d));
    }
};

/**
 * Reusable D3.js Scatter Plot Renderer
 * Correlates two metrics and visualizes data points with tooltips and optional categorization.
 */
window.drawScatterPlot = function(containerSelector, data, xKey, yKey, options = {}) {
    const margin = {top: 30, right: 30, bottom: 50, left: 60};
    const container = document.querySelector(containerSelector);
    if (!container || !data || data.length === 0) return;
    
    // Clear previous chart
    d3.select(containerSelector).selectAll("*").remove();
    
    const totalWidth = container.clientWidth || 800;
    const totalHeight = 500;
    const width = totalWidth - margin.left - margin.right;
    const height = totalHeight - margin.top - margin.bottom;

    const svg = d3.select(containerSelector)
        .append("svg")
        .attr("viewBox", `0 0 ${totalWidth} ${totalHeight}`)
        .attr("preserveAspectRatio", "xMidYMid meet")
        .attr("width", "100%")
        .append("g")
        .attr("transform", `translate(${margin.left},${margin.top})`);

    // Ensure numeric data and skip invalid/null values
    const cleanData = data.filter(d => 
        d[xKey] != null && d[yKey] != null && 
        !isNaN(d[xKey]) && !isNaN(d[yKey])
    ).map(d => ({
        ...d,
        xVal: Number(d[xKey]),
        yVal: Number(d[yKey])
    }));

    if (cleanData.length === 0) {
        document.querySelector(containerSelector).innerHTML = '<p class="chart-no-data">Insufficient numeric data for scatter plot.</p>';
        return;
    }

    // Determine domains allowing a bit of padding
    const xDomain = d3.extent(cleanData, d => d.xVal);
    const yDomain = d3.extent(cleanData, d => d.yVal);
    
    // Add 5% padding to domain
    const xPadding = (xDomain[1] - xDomain[0]) * 0.05;
    const yPadding = (yDomain[1] - yDomain[0]) * 0.05;

    const x = d3.scaleLinear()
        .domain([Math.max(0, xDomain[0] - xPadding), xDomain[1] + xPadding])
        .range([0, width]);

    const y = d3.scaleLinear()
        .domain([Math.max(0, yDomain[0] - yPadding), yDomain[1] + yPadding])
        .range([height, 0]);

    // Draw X Axis
    svg.append("g")
        .attr("transform", `translate(0,${height})`)
        .call(d3.axisBottom(x).ticks(8))
        .call(g => g.select(".domain").attr("stroke", "var(--text-muted)"))
        .call(g => g.selectAll(".tick line").attr("stroke", "var(--text-muted)"));

    // Draw Y Axis
    svg.append("g")
        .call(d3.axisLeft(y).ticks(8))
        .call(g => g.select(".domain").attr("stroke", "var(--text-muted)"))
        .call(g => g.selectAll(".tick line").attr("stroke", "var(--text-muted)"));
        
    // Format labels mapping keys to readable names
    const formatLabel = options.labelFormatter || (key => {
        const map = {
            'total_docs': 'Total Documents',
            'citable_docs_3y': 'Citable Docs (3Y)',
            'sjr_index': 'SJR Index'
        };
        return map[key] || key;
    });

    // Add X axis label
    svg.append("text")
        .attr("text-anchor", "middle")
        .attr("x", width / 2)
        .attr("y", height + margin.bottom - 10)
        .attr("fill", "var(--text-color)")
        .style("font-size", "0.9rem")
        .text(formatLabel(xKey));

    // Add Y axis label
    svg.append("text")
        .attr("text-anchor", "middle")
        .attr("transform", "rotate(-90)")
        .attr("y", -margin.left + 20)
        .attr("x", -height / 2)
        .attr("fill", "var(--text-color)")
        .style("font-size", "0.9rem")
        .text(formatLabel(yKey));

    // Calculate Pearson Correlation (optional)
    if (options.showCorrelation !== false && cleanData.length > 1) {
        const meanX = d3.mean(cleanData, d => d.xVal);
        const meanY = d3.mean(cleanData, d => d.yVal);
        let num = 0, denX = 0, denY = 0;
        cleanData.forEach(d => {
            const dx = d.xVal - meanX;
            const dy = d.yVal - meanY;
            num += dx * dy;
            denX += dx * dx;
            denY += dy * dy;
        });
        const r = num / Math.sqrt(denX * denY);
        
        svg.append("text")
            .attr("x", width)
            .attr("y", 10)
            .attr("text-anchor", "end")
            .attr("fill", "var(--text-muted)")
            .style("font-size", "0.85rem")
            .text(`r ≈ ${r.toFixed(2)} (${cleanData.length} records)`);
    }

    const tooltip = chartTooltip();
    const colorKey = options.colorKey || 'best_quartile';
    const colorMap = options.colorMap || {
        'Q1': '#10b981',
        'Q2': '#fde047',
        'Q3': '#f97316',
        'Q4': '#ef4444'
    };
    
    // Add dots
    svg.selectAll("circle")
        .data(cleanData)
        .enter()
        .append("circle")
        .attr("cx", d => x(d.xVal))
        .attr("cy", d => y(d.yVal))
        .attr("r", 5)
        .attr("fill", d => colorMap[d[colorKey]] || '#38bdf8')
        .attr("opacity", 0) // start invisible for animation
        .attr("stroke", "var(--background-color)")
        .attr("stroke-width", 1)
        .on("mouseover", function(event, d) {
            d3.select(this)
              .transition().duration(200)
              .attr("r", 8)
              .attr("stroke", "#fff")
              .attr("stroke-width", 2);
              
            tooltip.transition().duration(200).style("opacity", .9);
            
            // Fix tooltip rendering using the existing chartTooltip which sets innerHTML
            tooltip.html(`
                <div style="font-weight:bold; margin-bottom:4px; max-width:250px; white-space:normal;">${escapeHtml(d.title || 'Unknown')}</div>
                <div style="display:flex; justify-content:space-between; width:100%; margin-bottom: 2px;">
                    <span style="color:var(--text-muted); margin-right: 8px;">${formatLabel(xKey)}:</span> 
                    <strong>${d.xVal.toLocaleString()}</strong>
                </div>
                <div style="display:flex; justify-content:space-between; width:100%;">
                    <span style="color:var(--text-muted); margin-right: 8px;">${formatLabel(yKey)}:</span> 
                    <strong>${d.yVal.toLocaleString()}</strong>
                </div>
                ${d[colorKey] ? `<div style="margin-top:4px; font-size:0.8rem; padding-top: 4px; border-top: 1px solid rgba(255,255,255,0.1);">Quartile: ${d[colorKey]}</div>` : ''}
            `)
            .style("left", (event.pageX + 10) + "px")
            .style("top", (event.pageY - 28) + "px");
        })
        .on("mouseout", function() {
            d3.select(this)
              .transition().duration(300)
              .attr("r", 5)
              .attr("stroke", "var(--background-color)")
              .attr("stroke-width", 1);
            tooltip.transition().duration(500).style("opacity", 0);
        })
        .transition()
        .delay((d, i) => Math.random() * 800) // Staggered entrance
        .duration(800)
        .attr("opacity", 0.7);
};
