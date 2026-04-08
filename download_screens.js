const fs = require('fs');
const { execSync } = require('child_process');

const data = JSON.parse(fs.readFileSync('screens_data.json', 'utf8'));

for (const [id, screen] of Object.entries(data)) {
  const content = screen.structuredContent;
  const title = content.title.replace(/[^a-zA-Z0-9]/g, '_').replace(/_+/g, '_');
  
  const htmlUrl = content.htmlCode.downloadUrl;
  const imgUrl = content.screenshot.downloadUrl;
  
  console.log(`Downloading ${title}...`);
  
  const htmlCmd = `curl -sL "${htmlUrl}" -o "${title}.html"`;
  execSync(htmlCmd);
  
  const imgCmd = `curl -sL "${imgUrl}" -o "${title}.png"`;
  execSync(imgCmd);
}
console.log("Done downloading all files.");
