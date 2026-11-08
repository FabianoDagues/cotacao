// Deterministic scanner/UI tests: no camera, network or browser required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../app/static/internal_scanner.js'),'utf8');
function element() { return {disabled:false,hidden:false,textContent:'',value:'',events:{},addEventListener(n,fn){this.events[n]=fn;},dispatchEvent(){},focus(){this.focused=true;}}; }
function setup(mode='success') {
  const open=element(), stop=element(), status=element(), target=element();let submissions=0;
  const root={dataset:{scannerTarget:'barcode',scannerSubmit:'form'},querySelector(s){return {'.camera-open':open,'.camera-stop':stop,'.camera-status':status}[s];}};
  const document={hidden:false,events:{},querySelector(){return root;},getElementById(id){return id==='barcode'?target:{requestSubmit(){submissions++;}};},addEventListener(n,fn){this.events[n]=fn;}};
  let instance;
  class Scanner {
    constructor(){instance=this;this.stops=0;}
    async start(_camera,_config,callback){this.callback=callback;if(mode==='denied')throw Error('Denied');if(mode==='pending')await new Promise(resolve=>this.resolve=resolve);}
    async stop(){this.stops++;}
  }
  const window={isSecureContext:true,addEventListener(){}};
  const context={document,window,navigator:{mediaDevices:{getUserMedia(){}}},Event:class{},Html5QrcodeSupportedFormats:{},Html5Qrcode:mode==='missing'?undefined:Scanner};
  vm.runInNewContext(source,context);
  return {open,stop,status,target,document,get instance(){return instance;},get submissions(){return submissions;}};
}
(async()=>{
 let app=setup();await app.open.events.click();assert.equal(app.open.disabled,true);assert.equal(app.stop.hidden,false);
 await app.instance.callback('7891234567895');await app.instance.callback('7891234567895');
 assert.equal(app.submissions,1);assert.equal(app.target.value,'7891234567895');assert.equal(app.instance.stops,1);assert.equal(app.open.disabled,false);assert.equal(app.stop.hidden,true);
 app=setup('denied');await app.open.events.click();assert.match(app.status.textContent,/Câmera indisponível/);assert.equal(app.open.disabled,false);
 app=setup('missing');await app.open.events.click();assert.match(app.status.textContent,/Não foi possível carregar/);assert.equal(app.submissions,0);
 app=setup('pending');const starting=app.open.events.click();await app.stop.events.click();app.instance.resolve();await starting;assert.equal(app.instance.stops,1);assert.equal(app.stop.hidden,true);assert.equal(app.submissions,0);
 app=setup();await app.open.events.click();app.document.hidden=true;app.document.events.visibilitychange();await new Promise(r=>setImmediate(r));assert.equal(app.instance.stops,1);
 const formSource=fs.readFileSync(path.join(__dirname,'../app/static/internal_forms.js'),'utf8');const submit=element();let scrolled=false;const form={events:{},addEventListener(n,fn){this.events[n]=fn;},querySelector(){return submit;}};const window={events:{},addEventListener(n,fn){this.events[n]=fn;}};
 vm.runInNewContext(formSource,{window,document:{getElementById(id){return {'withdraw-save':form,'selected-product':{scrollIntoView(){scrolled=true;}}}[id];}}});
 form.events.submit();assert.equal(submit.disabled,true);window.events.pageshow();assert.equal(submit.disabled,false);assert.equal(scrolled,true);
 console.log('PASS: scanner success, duplicate scan, permission denial, missing library, cancel during start, background shutdown, submit/bfcache recovery.');
})().catch(error=>{console.error(error);process.exit(1);});
