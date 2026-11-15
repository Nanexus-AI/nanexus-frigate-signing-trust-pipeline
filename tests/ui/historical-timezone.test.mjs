import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM, ui, PAGE} from './assets.mjs';

// Deliberately different from the configured Frigate zone.
process.env.TZ='Asia/Tokyo';
const source=ui('historical.js').toString();
const REQUEST='0123456789abcdef0123456789abcdef';
const json=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
async function until(fn){const end=Date.now()+3000;while(!fn()){assert.ok(Date.now()<end,'UI timeout');await new Promise(r=>setTimeout(r,10));}}
async function open(t,{config={ui:{timezone:'America/Toronto'}},configStatus=200,coverage=true}={}){
  const dom=new JSDOM('<div id="root"></div>',{url:PAGE,runScripts:'dangerously',pretendToBeVisual:true});
  const w=dom.window,root=w.document.getElementById('root'),calls=[],submissions=[];
  w.Date.now=()=>Date.parse('2026-07-01T01:00:00Z');
  w.Blob.prototype.text=function(){return new Promise((resolve,reject)=>{const r=new w.FileReader();r.onload=()=>resolve(String(r.result));r.onerror=()=>reject(r.error);r.readAsText(this);});};
  let range;
  w.fetch=async(path,options={})=>{
    calls.push({path:String(path),options});
    if(path==='/api/config')return json(config,configStatus);
    if(path==='/api/version')return new Response('0.18.2-cfa08c5');
    if(path==='/__nanexus/session')return json({principal:'viewer',operations_available:true,cameras:[{camera_id:'cam',display_name:'Camera'}]});
    if(String(path).startsWith('/__nanexus/window/status?')){
      const q=new URL(path,PAGE).searchParams;
      return json({schema:'nanexus-hv2-catalog-days/1',camera:'cam',month:q.get('month'),zone:q.get('zone'),dates:[q.get('month')+'-30'],complete:true});
    }
    if(path==='/__nanexus/window/verify'){
      range=JSON.parse(options.body);submissions.push(range);return json({request_id:REQUEST,state:'RUNNING'});
    }
    if(String(path).startsWith('/__nanexus/window/progress?')){
      const a=range.start,b=range.end,m=String(Number(a)+60);
      return json({schema:'nanexus-hv2-window-progress/1',request_id:REQUEST,camera:'cam',state:'COMPLETE',files:[{file_id:'a',recording:'catalog-recording',position:0,metadata_interval:{start:a,end:m},state:'COMPLETED',finding:'GREEN'}],
        ...(coverage?{query_coverage:{schema:'nanexus-hv2-query-coverage/1',query:{start:a,end:b},dataset:{start:a,end:b},segments:[{kind:'recording',start:a,end:m},{kind:'gap',start:m,end:b}]}}:{}),reason:null,evidence_available:false});
    }
    if(path==='/__nanexus/window/release')return json({request_id:REQUEST,released:true});
    throw Error('Unexpected request '+path);
  };
  w.eval(source);
  const close=w.__nanexusHistorical.mount(root);
  t.after(()=>{close();w.close();});
  if(configStatus===401||configStatus===403){await until(()=>root.querySelector('#historical-state').textContent.length>0);}
  else await until(()=>root.querySelector('select[aria-label=Camera] option[value=cam]'));
  function choose(date,start,end){
    const dateInput=root.querySelector('input[aria-label=Date]');dateInput.value=date;dateInput.dispatchEvent(new w.Event('input'));
    for(const [name,value] of [['Start hour',+start.split(':')[0]],['Start minute',+start.split(':')[1]],['End hour',+end.split(':')[0]],['End minute',+end.split(':')[1]]]){
      const c=root.querySelector(`select[aria-label="${name}"]`);c.value=String(value);c.dispatchEvent(new w.Event('change'));
    }
  }
  async function submit(){root.querySelector('form').dispatchEvent(new w.Event('submit',{cancelable:true}));await new Promise(r=>setTimeout(r,40));}
  return {root,w,calls,submissions,choose,submit};
}

test('A configured timezone controls calendar, Catalog zone, input, results and accessible labels',async t=>{
  const h=await open(t);
  assert.equal(new Intl.DateTimeFormat().resolvedOptions().timeZone,'Asia/Tokyo');
  assert.equal(h.root.querySelector('#historical-zone').textContent,'America/Toronto');
  assert.match(h.root.querySelector('#historical-zone').title,/Frigate ui.timezone/);
  assert.equal(h.root.querySelector('.nx-month').textContent,'June 2026 America/Toronto');
  await until(()=>h.calls.some(c=>c.path.includes('/window/status?')));
  const q=new URL(h.calls.find(c=>c.path.includes('/window/status?')).path,PAGE).searchParams;
  assert.equal(q.get('month'),'2026-06');assert.equal(q.get('zone'),'America/Toronto');
  await until(()=>h.root.querySelector('.nx-has-recording'));
  assert.equal(h.root.querySelector('.nx-has-recording').getAttribute('aria-label'),'2026-06-30 America/Toronto has Catalog recordings');
  h.choose('2026-01-15','14:15','14:30');await h.submit();
  assert.deepEqual(h.submissions,[{camera:'cam',start:'1768504500',end:'1768505400'}]);
  await until(()=>h.root.querySelector('.nx-cov-interval'));
  const recording=h.root.querySelector('.nx-cov-interval'),gap=h.root.querySelector('.nx-cov-gap');
  assert.match(recording.title,/14:15:00 GMT-5/);assert.match(recording.title,/America\/Toronto/);
  assert.match(recording.title,/Not authenticated signing times/);assert.match(recording.title,/1768504500/);
  assert.equal(recording.getAttribute('aria-label'),recording.title);
  assert.match(gap.title,/America\/Toronto/);assert.equal(gap.getAttribute('aria-label'),gap.title);
  assert.match(h.root.querySelector('#historical-coverage').getAttribute('aria-label'),/America\/Toronto/);
  assert.match(h.root.querySelector('#historical-range').textContent,/14:15:00/);
  assert.ok(recording.classList.contains('nx-cov-green'));
  assert.match(h.root.querySelector('#historical-summary').textContent,/1 green/);
  for(const call of h.calls){assert.equal(call.options.credentials,'same-origin');assert.equal(call.options.redirect,'error');}
  const post=h.calls.find(c=>c.path==='/__nanexus/window/verify');assert.equal(post.options.headers['X-Nanexus-Request'],'1');
});

test('B unset Frigate timezone follows browser timezone',async t=>{
  const h=await open(t,{config:{ui:{timezone:null}}});
  assert.equal(h.root.querySelector('#historical-zone').textContent,'Asia/Tokyo');
  assert.match(h.root.querySelector('#historical-zone').title,/Frigate default/);
  assert.equal(h.root.querySelector('.nx-month').textContent,'July 2026 Asia/Tokyo');
  h.choose('2026-01-15','14:15','14:30');await h.submit();
  assert.deepEqual(h.submissions,[{camera:'cam',start:'1768454100',end:'1768455000'}]);
});

test('C Toronto summer conversion and fallback file-grid labels',async t=>{
  const h=await open(t,{coverage:false});h.choose('2026-07-15','14:15','14:30');await h.submit();
  assert.deepEqual(h.submissions,[{camera:'cam',start:'1784139300',end:'1784140200'}]);
  await until(()=>h.root.querySelector('.nx-file-grid [role=listitem]'));
  const item=h.root.querySelector('.nx-file-grid [role=listitem]');
  assert.match(item.title,/14:15:00/);assert.match(item.title,/America\/Toronto/);
  assert.equal(item.getAttribute('aria-label'),item.title);
});

test('E DST gaps and overlaps are rejected explicitly without a request',async t=>{
  for(const [date,start,end,pattern] of [
    ['2026-03-08','02:15','03:15',/does not exist.*Choose a time outside the skipped interval/],
    ['2026-11-01','01:15','01:30',/repeated.*Choose a time outside the repeated interval/],
  ]){
    const h=await open(t);h.choose(date,start,end);await h.submit();
    assert.equal(h.submissions.length,0);assert.match(h.root.querySelector('#historical-state').textContent,pattern);
  }
});

test('F actual duration accepts spring 90 wall minutes as 30 actual minutes',async t=>{
  const h=await open(t);h.choose('2026-03-08','01:45','03:15');
  assert.equal(h.root.querySelector('button[type=submit]').disabled,false);
  assert.equal(h.root.querySelector('select[aria-label="End minute"] option[value="15"]').disabled,false);
  assert.equal(h.root.querySelector('select[aria-label="End minute"] option[value="16"]').disabled,true);
  await h.submit();
  assert.deepEqual(h.submissions,[{camera:'cam',start:'1772952300',end:'1772954100'}]);
});

test('F actual duration rejects spring 31 minutes, fall extension and reverse intervals',async t=>{
  for(const [date,start,end] of [['2026-03-08','01:45','03:16'],['2026-11-01','00:45','02:00'],['2026-01-15','14:30','14:15']]){
    const h=await open(t);h.choose(date,start,end);await h.submit();
    assert.equal(h.submissions.length,0);assert.match(h.root.querySelector('#historical-state').textContent,/increasing interval of at most 30 minutes/);
  }
});

test('fractional-hour timezone uses actual IANA offset',async t=>{
  const h=await open(t,{config:{ui:{timezone:'Asia/Kathmandu'}}});
  h.choose('2026-01-15','14:15','14:30');await h.submit();
  assert.deepEqual(h.submissions,[{camera:'cam',start:'1768465800',end:'1768466700'}]);
});

test('unavailable or invalid config labels browser fallback explicitly',async t=>{
  for(const options of [{configStatus:503},{config:{ui:{timezone:'Invalid/Zone'}}}]){
    const h=await open(t,options);
    assert.equal(h.root.querySelector('#historical-zone').textContent,'Asia/Tokyo (browser fallback)');
    assert.match(h.root.querySelector('#historical-zone').title,/Frigate timezone unavailable/);
    h.choose('2026-01-15','14:15','14:30');await h.submit();
    assert.equal(h.submissions[0].start,'1768454100');
  }
});

test('H config authorization failure cannot re-admit a resolved session',async t=>{
  const h=await open(t,{configStatus:401});
  assert.equal(h.root.querySelector('select[aria-label=Camera]').disabled,true);
  assert.equal(h.root.querySelector('button[type=submit]').disabled,true);
  assert.equal(h.submissions.length,0);
  assert.equal(h.calls.filter(c=>c.path.includes('/window/status?')).length,0);
});


test('half-hour DST transition rejects Lord Howe gap and overlap',async t=>{
  for(const [date,start,end,pattern] of [
    ['2026-10-04','02:15','02:45',/does not exist/],
    ['2026-04-05','01:45','02:00',/repeated/],
  ]){
    const h=await open(t,{config:{ui:{timezone:'Australia/Lord_Howe'}}});
    h.choose(date,start,end);await h.submit();
    assert.equal(h.submissions.length,0);assert.match(h.root.querySelector('#historical-state').textContent,pattern);
  }
});
