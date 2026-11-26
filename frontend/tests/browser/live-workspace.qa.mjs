// Isolated browser regression: synthetic API responses, real browser microphone capture.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
const output = process.env.LIVE_QA_OUTPUT || 'test-results/live-workspace';
const baseURL = process.env.LIVE_QA_BASE_URL || 'http://127.0.0.1:5183';
const apiOrigin = process.env.LIVE_QA_API_ORIGIN || 'http://127.0.0.1:8010';
await mkdir(output, {recursive:true});
const browser = await chromium.launch({channel:'chrome', headless:true, args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream','--autoplay-policy=no-user-gesture-required']});
try {
const context = await browser.newContext({viewport:{width:1680,height:1000},permissions:['microphone'],reducedMotion:'reduce'});
const page = await context.newPage();
const errors=[]; page.on('pageerror',error=>errors.push(error.message)); page.on('console',msg=>{if(msg.type()==='error')console.log('browser-console:'+msg.text())});
let audioChunks=0, endCount=0, liveCount=0, cart=[];
const session='session-qa'; const recording='00000000-0000-0000-0000-000000000011';
const customer={name:'QA customer',phone:'9876543210',email:'qa@example.test'};
const items=[{match_status:'verified',matched_specs:['1200 mm sweep','BLDC'],availability_status:'in_stock',price_status:'known',item_id:'fan-1',name:'BLDC ceiling fan 1200 mm',sku:'FAN-BLDC-1200',rate:4499,stock_on_hand:12,description:'1200 mm sweep\nRemote control\nLow noise'},{match_status:'needs_confirmation',matched_specs:['1 HP'],missing_specs:['Electrical phase'],availability_status:'needs_review',price_status:'known',item_id:'motor-1',name:'Single phase motor 1 HP',sku:'MOTOR-1HP',rate:6999,stock_on_hand:7,description:'1 HP\nSingle phase\nCopper winding'},{item_id:'unknown-price',name:'Price pending pump',rate:0,price_status:'confirmation_required',availability_status:'needs_review',match_status:'needs_confirmation',missing_specs:['Voltage']}];
const turns=[{id:'turn-1',start_seconds:0,end_seconds:5,english_text:'I need two ceiling fans and a motor for the new office.',speaker_ref:'speaker_0',speaker_role:'customer'},{id:'turn-2',start_seconds:5,end_seconds:9,english_text:'Would you prefer BLDC fans with remote control?',speaker_ref:'speaker_1',speaker_role:'agent'}];
const snapshot=()=>({id:recording,created_at:'2026-10-28T10:50:00Z',capture_state:endCount?'stopped':'recording',upload_state:endCount?'saved':'uploading',transcription_state:audioChunks>=2?'complete':'pending',english_state:audioChunks>=2?'available':'pending',final_state:endCount?'complete':'pending'});
await page.route('**/*',async route=>{
 const request=route.request(); const url=new URL(request.url());
 if(!['http:','https:'].includes(url.protocol))return route.continue();
 if(!['127.0.0.1','localhost'].includes(url.hostname))return route.abort('blockedbyclient');
 if(!url.pathname.startsWith('/api/')&&!url.pathname.startsWith('/uploads/'))return route.continue();
 if(request.method()==='OPTIONS') return route.fulfill({status:204,headers:{'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'*','Access-Control-Allow-Methods':'GET,POST,PUT,PATCH,DELETE,OPTIONS'}});
 let data={}; const path=url.pathname.replace(/^\/api(?:\/v1)?/,'');
 if(url.pathname.startsWith('/uploads/'))data={};
 else if(path==='/chats')data=[{id:session,...customer,updated_at:'2026-10-28T10:50:00Z'}];
 else if(path==='/session-contacts')data={contacts:[{id:'contact-1',session_id:session,...customer,sources:['Enquiry'],enquiry_stage:'new',enquiry_id:'enquiry-qa'}]};
 else if(path===`/voice-sessions/${session}/history`)data={session:customer,messages:[],recorder_sessions:[],conversation_id:null};
 else if(path===`/voice-sessions/${session}/notes`)data=request.method()==='POST'?{note:{id:'note-1',content:request.postDataJSON().content,created_at:new Date().toISOString(),author_name:'QA agent'}}:{notes:[]};
 else if(path===`/voice-sessions/${session}/context`){if(request.method()==='PUT')cart=request.postDataJSON().cart;data={product_requirements:[],cart};}
 else if(path===`/voice-sessions/${session}/live`){++liveCount;data={conversation_id:recording,recording:snapshot(),transcript_preview:audioChunks>=2?turns:[],keywords:audioChunks>=2?['Ceiling fan','BLDC','Remote control','Motor']:[],recommendations:audioChunks>=2?items:[],insights:[],analysis_status:audioChunks>=2?'complete':'waiting_for_translation',catalogue_status:audioChunks>=2?'ready':'waiting',clarification_suggestions:audioChunks>=2?[{id:'motor-phase',requirement_id:'motor',question:'What electrical phase is available for the motor?',missing_fields:['phase'],evidence:'a motor for the new office'}]:[],current_intent:audioChunks>=2?{summary:'Select office fans and a compatible motor'}:null,notices:[]};}
 else if(path==='/products')data={items};
 else if(path==='/document-contacts')data={contacts:[{...customer,contact_id:'books-1'}]};
 else if(path==='/zoho/status')data={connected:true,organization_id:'books-org'};
 else if(path==='/engine-settings')data={default_engine_id:'elevenlabs'};
 else if(path==='/connections')data={items:[{engine_id:'elevenlabs',state:'connected'},{engine_id:'openai-translator',state:'connected'}]};
 else if(path==='/conversations'&&request.method()==='POST')data={id:recording};
 else if(path.endsWith('/lease'))data={part_id:'00000000-0000-0000-0000-000000000012',lease_token:'qa-lease'};
 else if(path.endsWith('/chunks/authorize'))data={upload_url:`${apiOrigin}/api/v1/local-audio/${request.postDataJSON().sequence}`,method:'PUT'};
 else if(path.endsWith('/chunks/confirm')){++audioChunks;data={confirmed:true};}
 else if(path.endsWith('/finalize')){++endCount;data={saved:true};}
 else if(path===`/conversations/${recording}`)data=snapshot();
 await route.fulfill({status:200,contentType:'application/json',headers:{'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'*','Access-Control-Allow-Methods':'GET,POST,PUT,PATCH,DELETE,OPTIONS'},body:JSON.stringify(data)});
});
await page.goto(`${baseURL}/ai-chats`);
await page.getByRole('button',{name:/QA customer/}).first().click();
await page.getByRole('checkbox',{name:/permission from all intended participants/}).check();
await page.getByRole('button',{name:'Start session',exact:true}).click();
await page.getByRole('button',{name:'Pause',exact:true}).waitFor();
for(let attempt=0;attempt<40&&audioChunks<2;attempt++) await page.waitForTimeout(500);
assert.ok(audioChunks >= 2, `Expected two capture uploads; got ${audioChunks}`);
await page.screenshot({path:`${output}/recording-check.png`,fullPage:true});
await page.getByLabel('Live translated conversation').getByText(turns[0].english_text).waitFor({timeout:15000});
assert.equal(await page.getByRole('button',{name:'Expand latest transcript',exact:true}).count(),1,'Transcript starts collapsed');
assert.equal(await page.getByText(turns[0].english_text,{exact:true}).count(),1,'Speech is shown once by default');
await page.getByRole('button',{name:'Expand latest transcript',exact:true}).click();
await page.getByRole('button',{name:'Collapse latest transcript',exact:true}).click();
assert.equal(endCount,0,'Translation must appear before recording ends');
assert.ok(audioChunks>=2,'Five-second audio chunks must upload during capture');
const guidance=page.getByRole('region',{name:'Agent guidance'});
await guidance.getByText('What electrical phase is available for the motor?',{exact:true}).waitFor();
assert.equal(await page.getByLabel('Live translated conversation').getByText('What electrical phase is available for the motor?',{exact:true}).count(),0,'Clarifications stay with the human agent');
assert.equal(await page.getByRole('button',{name:'Price unavailable for Price pending pump',exact:true}).isDisabled(),true,'Unknown price cannot enter the cart');
assert.equal(await page.getByText('₹0.00',{exact:true}).count(),0,'An unknown price must never appear as free');
assert.equal(await page.getByRole('button',{name:'Add to cart Single phase motor 1 HP',exact:true}).isDisabled(),true,'Technical match needs agent acknowledgement');
await page.screenshot({path:`${output}/conversation-desktop.png`,fullPage:true});
await page.getByRole('button',{name:'Add to cart BLDC ceiling fan 1200 mm',exact:true}).first().click();
await page.locator('[data-layout="cart"]').waitFor();
await page.getByRole('checkbox',{name:'Review unverified match Single phase motor 1 HP',exact:true}).check();
await page.getByRole('button',{name:'Add to cart Single phase motor 1 HP',exact:true}).first().click();
await page.getByRole('spinbutton',{name:'Discount for BLDC ceiling fan 1200 mm',exact:true}).fill('10');
await page.getByRole('button',{name:'Increase quantity of BLDC ceiling fan 1200 mm',exact:true}).click();
await page.getByLabel('Add internal note', {exact:true}).fill('Customer needs two fans and one motor.');
await page.getByRole('button',{name:'Add note',exact:true}).click();
await page.getByText('Customer needs two fans and one motor.',{exact:true}).waitFor();
await page.screenshot({path:`${output}/cart-desktop.png`,fullPage:true});
// Reproduce the user's contacts-open cart: notes must follow the entire cart,
// and its totals must remain reachable above the fixed recording controls.
await page.getByRole('button',{name:'Show contacts',exact:true}).click();
await page.setViewportSize({width:1680,height:960});
await page.getByRole('button',{name:'Complete cart',exact:true}).scrollIntoViewIfNeeded();
const separated=await page.evaluate(()=>{
 const cart=document.querySelector('.lws-cart').getBoundingClientRect();
 const notes=document.querySelector('.lws-notes').getBoundingClientRect();
 const grid=document.querySelector('.lws-work-grid');
 return {ok:notes.top>=cart.bottom-1 || notes.left>=cart.right-1,cart:cart.toJSON(),notes:notes.toJSON(),rows:getComputedStyle(grid).gridTemplateRows,gridHeight:grid.clientHeight,lane:getComputedStyle(document.querySelector('.lws-cart-lane')).minHeight};
});
await page.screenshot({path:`${output}/cart-with-contacts.png`,fullPage:true});
assert.equal(separated.ok,true,`Internal notes must not overlap cart items or totals: ${JSON.stringify(separated)}`);
await page.getByRole('button',{name:'Focus cart',exact:true}).click();
for(const viewport of [{width:1280,height:900},{width:390,height:844}]){
 await page.setViewportSize(viewport);
 await page.screenshot({path:`${output}/cart-${viewport.width}.png`,fullPage:true});
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1);
 assert.equal(overflow,false,`No page overflow at ${viewport.width}px`);
}
await page.getByRole('button',{name:'End session',exact:true}).click();
await page.getByRole('button',{name:'Record again',exact:true}).waitFor({timeout:15000});
assert.equal(endCount,1); assert.equal(errors.length,0,errors.join('\n'));
console.log(JSON.stringify({passed:true,audioChunks,liveCount,endCount,cartItems:cart.length,screenshots:output,consoleErrors:errors}));
} finally { await browser.close(); }
