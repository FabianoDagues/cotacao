(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const form = $('loss-form'), scan = $('scan-code'), search = $('product-search'), code = $('product-code'), desc = $('product-description'), unit = $('product-unit'), qty = $('loss-qty'), cost = $('loss-cost'), status = $('lookup-status'), resultsBox = $('lookup-results'), tbody = $('lookup-body'), remember = $('remember-barcode');
  let requestId = 0, timer, controller, products = [], selected = -1, pending = false, choosingCode = false;
  function message(text, tone='muted') { status.textContent = text; status.className = 'loss-status ' + tone; }
  function clearResults() { products = []; selected = -1; tbody.replaceChildren(); resultsBox.hidden = true; }
  function invalidate() { clearTimeout(timer); if(controller) controller.abort(); requestId++; pending = false; }
  function updateTotal() {
    const a = decimal(qty.value), b = decimal(cost.value);
    $('loss-total').textContent = cost.value.trim() && Number.isFinite(a) && Number.isFinite(b) && a > 0 && b >= 0 ? (a*b).toLocaleString('pt-BR',{style:'currency',currency:'BRL'}) : '—';
  }
  function decimal(v) { const s = String(v).trim().replace(/^R\$\s*/, '').replace(/\s/g,''); if(!s) return NaN; const normalized = s.includes(',') ? s.replace(/\./g,'').replace(',','.') : s; return /^\d+(\.\d+)?$/.test(normalized) ? Number(normalized) : NaN; }
  function resetProduct() { code.value = ''; desc.value = ''; unit.value = 'UN'; qty.value = '1'; cost.value = ''; remember.checked = false; updateTotal(); }
  function selectProduct(p) {
    const different = code.value !== p.code || desc.value !== p.description || unit.value !== p.unit;
    code.value = p.code; desc.value = p.description; unit.value = p.unit;
    if(different) { qty.value = '1'; cost.value = ''; }
    if(!scan.value.trim() && p.barcode) scan.value = p.barcode;
    remember.checked = false;
    choosingCode = false; clearResults(); search.value = '';
    message('Produto localizado: '+p.description+'. Confira a quantidade e o motivo.', 'loss-ok');
    updateTotal(); qty.focus(); qty.select();
  }
  function highlight(i) {
    if(!products.length) return;
    selected = Math.max(0,Math.min(i,products.length-1));
    Array.from(tbody.rows).forEach((r,n)=>r.classList.toggle('loss-selected',n===selected));
    if(tbody.rows[selected]) tbody.rows[selected].scrollIntoView({block:'nearest'});
  }
  function showProducts(items) {
    products = items; selected = -1; tbody.replaceChildren();
    items.forEach((p,i)=>{
      const tr = document.createElement('tr');
      [p.code || p.barcode || '—',p.description,p.unit].forEach(v=>{ const td=document.createElement('td'); td.textContent=v; tr.append(td); });
      const td=document.createElement('td'), button=document.createElement('button'); button.type='button'; button.className='compact'; button.textContent='Selecionar'; button.addEventListener('click',()=>selectProduct(p)); td.append(button); tr.append(td); tr.addEventListener('click',e=>{if(e.target!==button)selectProduct(p);}); tbody.append(tr);
    });
    resultsBox.hidden = items.length===0;
  }
  async function lookup(term, mode) {
    invalidate(); const id=requestId; controller=new AbortController(); pending=true; clearResults();
    if(mode==='code'){ resetProduct(); choosingCode=true; search.value=''; }
    message('Procurando produto…');
    try {
      const resp=await fetch('/admin/baixas/buscar-produtos?'+new URLSearchParams({q:term,modo:mode}),{signal:controller.signal,headers:{Accept:'application/json'},cache:'no-store'});
      if(!resp.ok) throw new Error('Busca indisponível');
      const data=await resp.json(); if(id!==requestId)return;
      if(mode==='code' && data.products.length===1 && !data.more) { selectProduct(data.products[0]); return; }
      showProducts(data.products);
      if(!data.products.length) {
        if(mode==='code'){code.value=term; choosingCode=false; message('Código não localizado. Busque pela descrição ou preencha o produto manualmente. Se desejar, marque o vínculo antes de salvar.','loss-warn'); search.focus();}
        else message('Nenhum produto localizado. Você pode preencher a descrição manualmente.','loss-warn');
      } else {
        message((mode==='code'?'Há mais de um produto com esse código. Escolha o correto. ':data.products.length+' produto(s) localizado(s). ')+(data.more?'Refine a busca para ver outros resultados.':'Clique em Selecionar ou use as setas.'));
        highlight(0);
      }
    } catch(e) {
      if(id!==requestId || e.name==='AbortError')return;
      if(mode==='code'){code.value=term; choosingCode=false;}
      message('Não foi possível consultar os produtos. Confira a conexão e tente novamente, ou preencha manualmente.','loss-warn');
    } finally { if(id===requestId) pending=false; }
  }
  function scanNow(){const value=scan.value.trim(); if(!value){scan.focus();return;} lookup(value,'code');}
  scan.addEventListener('input',()=>{invalidate();clearResults();remember.checked=false;choosingCode=!!scan.value.trim();if(choosingCode)message('Pressione Enter ou Localizar para consultar o código.');});
  scan.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();if(products.length&&selected>=0)selectProduct(products[selected]);else scanNow();}else if(e.key==='ArrowDown'||e.key==='ArrowUp'){if(products.length){e.preventDefault();highlight(selected+(e.key==='ArrowDown'?1:-1));}}});
  $('scan-button').addEventListener('click',scanNow);
  search.addEventListener('input',()=>{invalidate();clearResults();const term=search.value.trim();if(term.length>=2){pending=true;timer=setTimeout(()=>lookup(term,'description'),250);}else message('Digite pelo menos duas letras para buscar.');});
  search.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();if(products.length&&selected>=0)selectProduct(products[selected]);else if(search.value.trim().length>=2)lookup(search.value.trim(),'description');}else if(e.key==='ArrowDown'||e.key==='ArrowUp'){if(products.length){e.preventDefault();highlight(selected+(e.key==='ArrowDown'?1:-1));}}});
  $('manual-product').addEventListener('click',()=>{invalidate();clearResults();choosingCode=false;search.value='';if(scan.value.trim()&&!code.value)code.value=scan.value.trim();message('Preencha ou confira a descrição e a unidade. O vínculo é opcional.');desc.focus();});
  function step(n){const v=decimal(qty.value);qty.value=String(Math.max(1,Math.round(((Number.isFinite(v)?v:1)+n)*10000)/10000)).replace('.',',');updateTotal();}
  $('qty-minus').addEventListener('click',()=>step(-1)); $('qty-plus').addEventListener('click',()=>step(1));
  qty.addEventListener('focus',()=>qty.select()); qty.addEventListener('input',updateTotal);cost.addEventListener('input',updateTotal);updateTotal();
  document.addEventListener('keydown',e=>{if(e.key==='F2'){e.preventDefault();scan.focus();scan.select();}else if(e.key==='F3'){e.preventDefault();search.focus();search.select();}});
  form.addEventListener('submit',e=>{if(pending||choosingCode){e.preventDefault();message('Conclua a busca ou escolha Preencher manualmente antes de salvar.','loss-warn');return;}if(remember.checked&&!scan.value.trim()){e.preventDefault();message('Informe o código lido para criar o vínculo.','loss-warn');scan.focus();return;} });
})();
