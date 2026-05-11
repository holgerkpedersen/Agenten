async function decompose() {
    const prompt = document.getElementById("promptInput").value;
    const filesData = window.loadedFilesContent || [];
    const response = await fetch("/api/decompose", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt, files: filesData })
    });
    const data = await response.json();
    visualizeTree(data.tree);
}

function visualizeTree(treeData) {
    const container = document.getElementById("treeContainer");
    container.innerHTML = "";
    const width = 800, height = 400;
    const svg = d3.select("#treeContainer")
        .append("svg")
        .attr("width", width)
        .attr("height", height);

    const root = d3.hierarchy(treeData);
    const treeLayout = d3.tree().size([width - 100, height - 100]);
    treeLayout(root);

    svg.selectAll(".link")
        .data(root.links())
        .enter()
        .append("line")
        .attr("class", "link")
        .attr("x1", d => d.source.x)
        .attr("y1", d => d.source.y)
        .attr("x2", d => d.target.x)
        .attr("y2", d => d.target.y)
        .attr("stroke", "#89b4fa")
        .attr("stroke-width", 2);

    svg.selectAll(".node")
        .data(root.descendants())
        .enter()
        .append("circle")
        .attr("class", "node")
        .attr("cx", d => d.x)
        .attr("cy", d => d.y)
        .attr("r", 8)
        .attr("fill", "#cba6f7");

    svg.selectAll(".label")
        .data(root.descendants())
        .enter()
        .append("text")
        .attr("x", d => d.x + 10)
        .attr("y", d => d.y + 4)
        .text(d => d.data.name.substring(0, 30))
        .attr("fill", "#cdd6f4")
        .attr("font-size", "12px");
}

async function executeTasks() {
    const response = await fetch("/execute", { method: "POST" });
    const data = await response.json();
    document.getElementById("result").innerText = JSON.stringify(data, null, 2);
    if (data.build_module) {
        alert("Agenten foreslår at bygge et nyt modul baseret på gentagne handlinger.");
    }
}

async function searchWeb() {
    const query = document.getElementById("promptInput").value;
    const response = await fetch("/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query })
    });
    const data = await response.json();
    document.getElementById("result").innerText = JSON.stringify(data.search_results, null, 2);
}