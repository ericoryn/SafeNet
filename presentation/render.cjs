const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const {chromium} = require('playwright');
const sharp = require('sharp');

(async () => {
  const candidates=[chromium.executablePath(),'C:/Program Files/Google/Chrome/Application/chrome.exe','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'];
  const executablePath=candidates.find(p=>fs.existsSync(p));
  assert(executablePath,'Нет доступного браузера для локального рендеринга');
  const browser=await chromium.launch({executablePath,headless:true,args:['--disable-gpu']});
  try {
    const page=await browser.newPage({viewport:{width:1280,height:760},deviceScaleFactor:1.25});
    await page.goto(pathToFileURL(path.join(__dirname,'SafeNet_KZ_Защита.html')).href);
    await page.evaluate(()=>document.fonts.ready);
    await page.emulateMedia({media:'print'});
    const issues=await page.evaluate(()=>[...document.querySelectorAll('.slide')].flatMap((slide,i)=>[...slide.querySelectorAll('.text')].flatMap(el=>el.scrollHeight>el.clientHeight+2||el.scrollWidth>el.clientWidth+2?[{slide:i+1,text:el.textContent,height:el.clientHeight,scrollHeight:el.scrollHeight,width:el.clientWidth,scrollWidth:el.scrollWidth}]:[])));
    const imageDir=path.join(__dirname,'Слайды');
    fs.mkdirSync(imageDir,{recursive:true});
    const tiles=[];
    const count=await page.locator('.slide').count();
    assert.equal(count,JSON.parse(fs.readFileSync(path.join(__dirname,'Слайды_и_источники.json'),'utf8')).length);
    for(let i=0;i<count;i++) {
      const file=path.join(imageDir,`Слайд_${String(i+1).padStart(2,'0')}.png`);
      await page.locator('.slide').nth(i).screenshot({path:file});
      const tile=await sharp(file).resize(480,270).png().toBuffer();
      tiles.push({input:tile,left:(i%3)*492,top:Math.floor(i/3)*282});
    }
    await sharp({create:{width:1464,height:Math.ceil(count/3)*282-12,channels:3,background:'#0b1320'}}).composite(tiles).png().toFile(path.join(__dirname,'Обзор_слайдов.png'));
    await page.pdf({path:path.join(__dirname,'SafeNet_KZ_Защита.pdf'),preferCSSPageSize:true,printBackground:true});
    const report={slides:count,renderer:'Chromium — синхронная HTML-версия',overflowIssues:issues,pptxNativeRenderingChecked:false};
    fs.writeFileSync(path.join(__dirname,'Проверка_вёрстки.json'),JSON.stringify(report,null,2),'utf8');
    console.log(JSON.stringify(report));
    assert.equal(issues.length,0,'Текст не помещается в отведённые блоки');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
