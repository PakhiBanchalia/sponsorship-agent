const { spawn } = require("child_process");
const fs = require("fs");

const project_id = "6790137956938804826";
const screens = [
  "7a40f0da2af14f7d9bb1018cbef7dd43",
  "384d1ce58b5746548539e4701cbe2d85",
  "7e55de3e5c754649a6603320b903c710"
];

const mcp = spawn("npx", ["-y", "mcp-remote", "https://stitch.googleapis.com/mcp", "--header", "X-Goog-Api-Key: AQ.Ab8RN6JILA78SwGIvEYZob2mqVsA63AXsxZnqW9mXuuds1Jo_A"]);

let out = "";
let results = {};
let currentScreenIdx = 0;
let requesting = false;

mcp.stdout.on("data", data => {
  out += data.toString();
});

mcp.stderr.on("data", data => {
  if (data.toString().includes("Proxy established successfully")) {
    requestScreen();
  }
});

function requestScreen() {
  if (currentScreenIdx >= screens.length) {
    fs.writeFileSync("screens_data.json", JSON.stringify(results, null, 2));
    console.log("Done");
    process.exit(0);
  }
  requesting = true;
  const screen_id = screens[currentScreenIdx];
  mcp.stdin.write(JSON.stringify({
    jsonrpc: "2.0", 
    id: currentScreenIdx + 1, 
    method: "tools/call",
    params: {
      name: "get_screen",
      arguments: { project_id, screen_id }
    }
  }) + "\n");
}

setInterval(() => {
  try {
    const lines = out.split("\n");
    for (const line of lines) {
      if (line.includes("\"jsonrpc\"")) {
        const parsed = JSON.parse(line);
        if (parsed.id === currentScreenIdx + 1 && requesting) {
          results[screens[currentScreenIdx]] = parsed.result;
          requesting = false;
          currentScreenIdx++;
          requestScreen();
        }
      }
    }
    // Clean up processed lines
    out = lines[lines.length - 1];
  } catch(e) {}
}, 200);

setTimeout(() => { console.log("timeout"); process.exit(1); }, 15000);
