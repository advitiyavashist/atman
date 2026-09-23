const {chromium}=require('/tmp/t976-final-browser/node_modules/playwright');
const fs=require('fs');
(async()=>{const browser=await chromium.launch({headless:true});const results=[];
for(const width of [1440,390]){const page=await browser.newPage({viewport:{width,height:900}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
for(const route of ['/', '/app/']){const r=await page.goto('http://127.0.0.1:18796'+route);await page.waitForTimeout(1500);const text=await page.locator('body').innerText();const sizing=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));await page.screenshot({path:'/tmp/t976-final-browser/'+(route=='/'?'legacy':'app')+'-'+width+'.png',fullPage:true});results.push({width,route,status:r.status(),sizing,errors:[...errors],text});}await page.close();}
fs.writeFileSync('/tmp/t976-final-browser/results.json',JSON.stringify(results,null,2));console.log(JSON.stringify(results.map(({text,...r})=>({...r,text:text.slice(0,400)})),null,2));await browser.close();})();
