/** Browser/WebGL check of real packets produced by smoke_weapon_sights.py. */
import {createServer} from 'node:http';
import {readFile,writeFile,readdir} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const output=resolve(process.argv[2]);
const esbuild=await import(pathToFileURL(process.argv[3]).href);
const {chromium}=await import(pathToFileURL(process.argv[4]).href);
const files=(await readdir(output)).filter(n=>n.endsWith('-model.json'));
const records=[];
for(const name of files){
  const model=JSON.parse(await readFile(resolve(output,name),'utf8'));
  const snapshot=JSON.parse(await readFile(resolve(output,name.replace('-model.json','-snapshot.json')),'utf8'));
  let animation=null;
  try{animation=JSON.parse(await readFile(resolve(output,name.replace('-model.json','-animation.json')),'utf8'));}
  catch{try{const a=JSON.parse(await readFile(resolve(output,'animation-samples.json'),'utf8'));if(a.weapon===snapshot.selected_weapon)animation=a;}catch{}}
  if(animation&&(animation.weapon!==snapshot.selected_weapon||animation.source!==model.source||animation.revision!==model.revision||animation.edition!==model.edition))throw new Error('Mismatched animation artifact: '+name);
  records.push({model,snapshot,animation,case:name.replace('-model.json','')});
}
await esbuild.build({entryPoints:[resolve(root,'scripts/weaponSightSmokeEntry.tsx')],bundle:true,format:'esm',jsx:'automatic',
  nodePaths:[resolve(root,'desktop/node_modules')],outfile:resolve(output,'sight-smoke.js'),target:'chrome120',define:{'process.env.NODE_ENV':'"production"'}});
const html=`<!doctype html><meta charset="utf-8"><title>Offline sight bench</title><style>
body{margin:24px;background:#141d1e;color:#e5ecec;font:16px system-ui;--text:#e5ecec;--muted:#b1c0c0;--font-label:14px;--font-caption:12px;--border-strong:#526867;--brand-500:#56bca2;--brand-600:#34977e;--surface:#203031;--surface-raised:#2b4141;--border:#435956}button,input,select,textarea{font:inherit;color:inherit;background:#263739;border:1px solid #506364;border-radius:5px;padding:8px}button{cursor:pointer}button:disabled{opacity:.4}p{line-height:1.5}label{display:grid;gap:6px}button:hover{background:#34534c}input[type=range]{padding:0}fieldset{border:1px solid #465758}h1{font-size:24px}
</style><link rel="stylesheet" href="/sight-smoke.css"><div id="root"></div><script type="module" src="/sight-smoke.js"></script>`;
const server=createServer(async(req,res)=>{
  try{
    if(req.url==='/cases.json'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify(records));}
    else if(req.url==='/'||req.url==='/index.html'){res.setHeader('Content-Type','text/html');res.end(html);}
    else if(['/sight-smoke.js','/sight-smoke.css'].includes(req.url)){res.setHeader('Content-Type',req.url.endsWith('js')?'text/javascript':'text/css');res.end(await readFile(resolve(output,req.url.slice(1))));}
    else {res.statusCode=404;res.end();}
  }catch(e){res.statusCode=500;res.end(String(e));}
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const url=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({executablePath:process.argv[5],headless:true,args:['--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:1});
const errors=[];page.on('pageerror',e=>errors.push(String(e)));
const checks=[];
try{
  await page.goto(url);await page.locator('canvas').waitFor();
  for(let i=0;i<records.length;i++){
    await page.getByLabel('Test weapon').selectOption(String(i));
    await page.locator('canvas').waitFor();
    const attachment=records[i].model.attachment;
    const name=records[i].case;
    const pixels=await page.locator('canvas').evaluate(canvas=>{
      const gl=canvas.getContext('webgl'),data=new Uint8Array(canvas.width*canvas.height*4);gl.readPixels(0,0,canvas.width,canvas.height,gl.RGBA,gl.UNSIGNED_BYTE,data);
      let changed=0;for(let n=0;n<data.length;n+=4)if(Math.abs(data[n]-data[0])+Math.abs(data[n+1]-data[1])+Math.abs(data[n+2]-data[2])>18)changed++;
      return changed;
    });
    if(pixels<1000)throw new Error(`${name}: empty geometry viewport (${pixels} pixels)`);
    await page.locator('.sight-canvas').screenshot({path:resolve(output,`${name}-side.png`)});
    if(attachment){
      const complete=await page.locator('canvas').evaluate(c=>c.toDataURL());
      await page.getByLabel('Show selected component').uncheck();
      await page.waitForFunction(previous=>document.querySelector('canvas').toDataURL()!==previous,complete);
      await page.getByLabel('Show selected component').check();
      await page.waitForFunction(previous=>document.querySelector('canvas').toDataURL()===previous,complete);
    }
    await page.getByLabel('View',{exact:true}).selectOption('aim');
    await page.locator('.sight-canvas').screenshot({path:resolve(output,`${name}-aim.png`)});
    if(attachment?.component_type==='CWeaponComponentScopeInfo')await page.getByLabel('Metadata family').selectOption('attached');
    const trialZ=attachment?.component_type==='CWeaponComponentScopeInfo'
      ?page.getByLabel('Sight trial Attached scope position Z',{exact:true}):page.getByLabel('Sight trial Scope position Z',{exact:true});
    const before=await page.locator('canvas').evaluate(c=>c.toDataURL());
    const old=await trialZ.inputValue();await trialZ.fill(String(Number(old)+.015));
    await page.waitForFunction(previous=>document.querySelector('canvas').toDataURL()!==previous,before);
    await page.getByLabel('Show saved baseline camera').check();
    await page.waitForFunction(previous=>document.querySelector('canvas').toDataURL()===previous,before);
    await page.getByLabel('Show saved baseline camera').uncheck();
    if(records[i].animation?.packet.selected){
      await page.getByLabel('View',{exact:true}).selectOption('side');
      const bindFrame=await page.locator('canvas').evaluate(c=>c.toDataURL());
      await page.getByLabel('Frozen animation time',{exact:true}).fill((records[i].animation.packet.duration*.5).toFixed(4));
      await page.waitForFunction(previous=>document.querySelector('canvas').toDataURL()!==previous,bindFrame);
      await page.locator('.sight-canvas').screenshot({path:resolve(output,`${name}-motion.png`)});
      await page.getByLabel('Freeze bind pose').check();
      await page.waitForFunction(previous=>document.querySelector('canvas').toDataURL()===previous,bindFrame);
    }
    checks.push({weapon:name,geometry_pixels:pixels,camera_trial_changed_frame:true,baseline_restored_exact_frame:true,
      attachment_visible_and_toggle_verified:!!attachment,animation_sampled:!!records[i].animation?.packet.selected});
  }
  await page.screenshot({path:resolve(output,'sight-layout.png'),fullPage:true});
  if(errors.length)throw new Error(errors.join('\n'));
  await writeFile(resolve(output,'browser-smoke.json'),JSON.stringify({checks,errors,engine:await browser.version()},null,2));
  console.log(JSON.stringify({checks,errors}));
}catch(e){
  await page.screenshot({path:resolve(output,'browser-failure.png'),fullPage:true});
  console.error(await page.locator('body').innerText());console.error(errors);throw e;
}finally{await browser.close();await new Promise(r=>server.close(r));}
