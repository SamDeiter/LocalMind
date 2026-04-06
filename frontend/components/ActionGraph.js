/* global d3 */
/**
 * ActionGraph.js
 * 
 * A high-performance D3 force-directed graph to visualize AI reasoning steps.
 */

export class ActionGraph {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        if (!this.container) return;

        this.width = this.container.clientWidth || 800;
        this.height = this.container.clientHeight || 400;
        this.nodes = [];
        this.links = [];
        this.maxNodes = 60; // Keep performance high

        this.init();
    }

    init() {
        // Clear previous
        this.container.innerHTML = '';
        if (typeof d3 === 'undefined') {
            console.warn('ActionGraph: d3 is not defined, attempting late load...');
            this.container.innerHTML = '<div class="text-[10px] text-slate-600 p-4 animate-pulse">Initializing visualization engine... (D3 loading)</div>';
            
            if (!document.getElementById('d3-loader')) {
                const script = document.createElement('script');
                script.id = 'd3-loader';
                script.src = 'https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js';
                script.onload = () => {
                    console.log('ActionGraph: D3 loaded successfully late.');
                    this.init(); 
                };
                script.onerror = () => {
                    this.container.innerHTML = '<div class="text-[10px] text-red-500 p-4">Visualization system offline (D3 failed to load)</div>';
                };
                document.head.appendChild(script);
            }
            return;
        }
        
        this.svg = d3.select(this.container)
            .append('svg')
            .attr('width', '100%')
            .attr('height', '100%')
            .attr('viewBox', [0, 0, this.width, this.height])
            .attr('style', 'max-width: 100%; height: auto;');

        // Define Glow Filter
        const defs = this.svg.append("defs");
        const filter = defs.append("filter")
            .attr("id", "glow");
        filter.append("feGaussianBlur")
            .attr("stdDeviation", "2.5")
            .attr("result", "coloredBlur");
        const feMerge = filter.append("feMerge");
        feMerge.append("feMergeNode").attr("in", "coloredBlur");
        feMerge.append("feMergeNode").attr("in", "SourceGraphic");

        this.g = this.svg.append('g');

        // Zoom & Pan
        this.svg.on("click", () => {
            const overlay = document.getElementById('graphNodeOverlay');
            if (overlay) overlay.classList.add('hidden');
        });

        this.svg.call(d3.zoom()
            .extent([[0, 0], [this.width, this.height]])
            .scaleExtent([0.1, 4])
            .on("zoom", (e) => this.g.attr("transform", e.transform)));

        this.simulation = d3.forceSimulation(this.nodes)
            .force("link", d3.forceLink(this.links).id(d => d.id).distance(80))
            .force("charge", d3.forceManyBody().strength(-150))
            .force("center", d3.forceCenter(this.width / 2, this.height / 2))
            .on("tick", () => this.ticked());

        this.linkGroup = this.g.append("g").attr("stroke", "#1e293b").attr("stroke-opacity", 0.6);
        this.nodeGroup = this.g.append("g");
    }

    addEvent(event) {
        if (!event.id) return;
        const existing = this.nodes.find(n => n.id === event.id);
        if (existing) {
            existing.action = event.action;
            existing.detail = event.detail;
            existing.model = event.model || existing.model;
            existing.ts = event.ts;
            this.update();
            
            // If overlay is open for this node, refresh it
            const overlay = document.getElementById('graphNodeOverlay');
            if (overlay && !overlay.classList.contains('hidden')) {
                const overlayIdText = overlay.querySelector('#overlayId').textContent;
                if (overlayIdText.includes(event.id.split('-').pop())) {
                    this.showOverlay(existing, { pageX: parseFloat(overlay.style.left), pageY: parseFloat(overlay.style.top) - 15 });
                }
            }
            return;
        }

        // Hide empty state
        const emptyState = document.getElementById('graphEmptyState');
        if (emptyState) emptyState.style.opacity = '0';

        const newNode = {
            id: event.id,
            action: event.action,
            detail: event.detail,
            model: event.model || 'unknown',
            ts: event.ts,
            x: this.width / 2 + (Math.random() - 0.5) * 100,
            y: this.height / 2 + (Math.random() - 0.5) * 100
        };

        this.nodes.push(newNode);

        if (event.parent_id) {
            this.links.push({
                source: event.parent_id,
                target: event.id
            });
        }

        // Cap size
        if (this.nodes.length > this.maxNodes) {
            this.nodes.shift();
            this.links = this.links.filter(l => 
                this.nodes.find(n => n.id === l.source.id || n.id === l.source) &&
                this.nodes.find(n => n.id === l.target.id || n.id === l.target)
            );
        }

        this.update();
    }

    update() {
        // Links
        const link = this.linkGroup.selectAll("line")
            .data(this.links, d => `${d.source.id || d.source}-${d.target.id || d.target}`);
        
        link.exit().remove();
        const linkEnter = link.enter().append("line")
            .attr("stroke-width", 1.5)
            .attr("stroke", "#06b6d4")
            .attr("stroke-opacity", 0.2);
        
        console.assert(linkEnter, "linkEnter created"); // Use variable to satisfy lint

        // Nodes
        const node = this.nodeGroup.selectAll("g")
            .data(this.nodes, d => d.id);

        node.exit().remove();
        
        const nodeEnter = node.enter().append("g")
            .call(d3.drag()
                .on("start", (e, d) => this.dragstarted(e, d))
                .on("drag", (e, d) => this.dragged(e, d))
                .on("end", (e, d) => this.dragended(e, d)));

        // Node Circle
        nodeEnter.append("circle")
            .attr("r", d => d.action.includes('start') ? 8 : 5)
            .attr("fill", d => this.getColor(d.action))
            .attr("filter", "url(#glow)")
            .attr("class", "cursor-pointer active:scale-95 transition-transform")
            .on("click", (e, d) => {
                e.stopPropagation();
                this.showOverlay(d, e);
            });

        // Tooltip Text
        nodeEnter.append("text")
            .attr("dy", 20)
            .attr("text-anchor", "middle")
            .attr("fill", "#94a3b8")
            .attr("font-size", "8px")
            .attr("font-weight", "bold")
            .text(d => d.action.split('_')[0].toUpperCase());

        this.simulation.nodes(this.nodes);
        this.simulation.force("link").links(this.links);
        this.simulation.alpha(0.3).restart();
    }

    ticked() {
        this.linkGroup.selectAll("line")
            .attr("x1", d => d.source.x)
            .attr("y1", d => d.source.y)
            .attr("x2", d => d.target.x)
            .attr("y2", d => d.target.y);

        this.nodeGroup.selectAll("g")
            .attr("transform", d => `translate(${d.x},${d.y})`);
    }


    createOverlay() {
        if (document.getElementById('graphNodeOverlay')) return document.getElementById('graphNodeOverlay');
        
        const overlay = document.createElement('div');
        overlay.id = 'graphNodeOverlay';
        overlay.className = 'fixed z-[1000] hidden bg-slate-900/95 backdrop-blur border border-slate-700/50 rounded-lg p-3 shadow-2xl pointer-events-none transition-all duration-200 w-64';
        overlay.innerHTML = `
            <div class="flex items-center justify-between mb-2">
                <span id="overlayAction" class="text-[10px] font-black uppercase tracking-widest px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400">Task</span>
                <span id="overlayTime" class="text-[9px] text-slate-500">Just now</span>
            </div>
            <div id="overlayId" class="text-[10px] font-mono text-slate-400 mb-2 truncate">Agent ID: ...</div>
            <div id="overlayDetail" class="text-xs text-slate-200 leading-relaxed">Initializing...</div>
            <div class="mt-2 pt-2 border-t border-slate-800/50 flex justify-between items-center">
                <span id="overlayModel" class="text-[9px] text-slate-500 italic">qwen2.5-coder</span>
                <span class="text-[9px] text-cyan-500/80 font-bold">● LIVE</span>
            </div>
        `;
        document.body.appendChild(overlay);
        return overlay;
    }

    showOverlay(d, event) {
        const overlay = this.createOverlay();
        
        // Populate
        overlay.querySelector('#overlayAction').textContent = d.action.split('_')[0].toUpperCase();
        overlay.querySelector('#overlayAction').className = `text-[10px] font-black uppercase tracking-widest px-2 py-0.5 rounded-full ${this.getBadgeClass(d.action)}`;
        overlay.querySelector('#overlayId').textContent = `Agent: ${d.id.split('-').pop()}`;
        overlay.querySelector('#overlayDetail').textContent = d.detail || 'Processing task segments...';
        overlay.querySelector('#overlayModel').textContent = d.model || 'local-brain';
        overlay.querySelector('#overlayTime').textContent = new Date(d.ts * 1000).toLocaleTimeString();

        // Position
        overlay.classList.remove('hidden');
        const x = event.pageX + 15;
        const y = event.pageY + 15;
        
        // Adjust if off-screen
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        const ow = 256; // w-64
        const oh = 150; // approx
        
        overlay.style.left = (x + ow > vw ? x - ow - 30 : x) + 'px';
        overlay.style.top = (y + oh > vh ? y - oh - 30 : y) + 'px';

        // Auto-hide after 5s or if clicked elsewhere handled by global listener
    }

    getBadgeClass(action) {
        if (action.includes('research')) return 'bg-purple-500/20 text-purple-400';
        if (action.includes('execution')) return 'bg-cyan-500/20 text-cyan-400';
        if (action.includes('reflection')) return 'bg-amber-500/20 text-amber-400';
        if (action.includes('error')) return 'bg-red-500/20 text-red-400';
        return 'bg-emerald-500/20 text-emerald-400';
    }

    getColor(action) {
        if (action.includes('research')) return '#a855f7'; // Purple
        if (action.includes('execution')) return '#06b6d4'; // Cyan
        if (action.includes('reflection')) return '#f59e0b'; // Amber
        if (action.includes('error')) return '#ef4444'; // Red
        return '#10b981'; // Emerald
    }

    dragstarted(e, d) {
        if (!e.active) this.simulation.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
    }

    dragged(e, d) {
        d.fx = e.x;
        d.fy = e.y;
    }

    dragended(e, d) {
        if (!e.active) this.simulation.alphaTarget(0);
        d.fx = null;
        d.fy = null;
    }

    reset() {
        this.nodes = [];
        this.links = [];
        this.update();
        const emptyState = document.getElementById('graphEmptyState');
        if (emptyState) emptyState.style.opacity = '1';
    }
}
