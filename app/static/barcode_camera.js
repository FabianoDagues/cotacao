(()=>{
  'use strict';
  const $=id=>document.getElementById(id), dialog=$('barcode-camera'), video=$('camera-video'), preview=$('camera-preview'), status=$('camera-status'), choices=$('camera-codes'), file=$('camera-file'), torch=$('camera-torch'), input=$('product-search');
  if(!dialog||!input)return;
  let stream=null,timer=null,controller=null,epoch=0,light=false,starting=false,previous='',consecutive=0;
  const canvas=document.createElement('canvas');
  const say=text=>{status.textContent=text;};
  function stop(){epoch++;clearTimeout(timer);timer=null;if(controller)controller.abort();controller=null;if(stream)stream.getTracks().forEach(t=>t.stop());stream=null;video.srcObject=null;preview.hidden=true;torch.hidden=true;light=false;starting=false;previous='';consecutive=0;}
  function close(){stop();if(dialog.open)dialog.close();}
  function choose(code){close();input.dispatchEvent(new CustomEvent('barcode-read',{detail:code}));}
  function showChoices(codes){stop();choices.replaceChildren();choices.hidden=false;say('Mais de um código encontrado. Escolha o código correto ou leia novamente.');for(const code of codes){const button=document.createElement('button');button.type='button';button.textContent=code;button.onclick=()=>choose(code);choices.append(button);}}
  function resetChoices(){choices.replaceChildren();choices.hidden=true;}
  function frame(source,w,h,crop){
    // Guia central: preserva a resolução horizontal das barras e exclui a maior parte do fundo.
    const sw=crop?Math.floor(w*.9):w,sh=crop?Math.floor(h*.64):h;
    const scale=Math.min(1,1600/Math.max(sw,sh));canvas.width=Math.max(1,Math.floor(sw*scale));canvas.height=Math.max(1,Math.floor(sh*scale));
    const ctx=canvas.getContext('2d',{alpha:false});ctx.drawImage(source,Math.floor((w-sw)/2),Math.floor((h-sh)/2),sw,sh,0,0,canvas.width,canvas.height);
    return new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.82));
  }
  async function decode(blob,id){
    if(!blob||id!==epoch||!dialog.open)return [];
    const abort=new AbortController();controller=abort;
    const timeout=setTimeout(()=>abort.abort(),8000);
    try{const response=await fetch('/admin/baixas/ler-camera',{method:'POST',body:blob,headers:{'Content-Type':'image/jpeg','Accept':'application/json'},signal:abort.signal,cache:'no-store',credentials:'same-origin'});
      if(response.status===403)throw Error('Sessão expirada ou sem permissão. Entre novamente no sistema.');
      if(response.status===429) return [];
      if(!response.ok)throw Error('Não foi possível analisar a imagem. Tente novamente ou use a busca manual.');
      const data=await response.json();return Array.isArray(data.codes)?data.codes:[];
    }catch(err){if(err.name==='AbortError'&&id===epoch)throw Error('A leitura demorou demais. Confira a conexão e tente novamente.');throw err;}
    finally{clearTimeout(timeout);if(controller===abort)controller=null;}
  }
  async function scan(id){
    if(id!==epoch||!dialog.open||!stream)return;
    try{
      if(video.readyState>=2&&video.videoWidth&&video.videoHeight){
        const blob=await frame(video,video.videoWidth,video.videoHeight,true);
        const codes=await decode(blob,id);if(id!==epoch||!dialog.open)return;
        if(codes.length>1){showChoices(codes);return;}
        if(codes.length===1){const code=codes[0];consecutive=code===previous?consecutive+1:1;previous=code;say('Código encontrado. Confirmando…');if(consecutive>=2){choose(code);return;}}
        else{previous='';consecutive=0;say('Aproxime o código, mantenha-o inteiro e evite reflexos.');}
      }
      timer=setTimeout(()=>scan(id),650);
    }catch(err){if(id!==epoch)return;stop();say(err.message||'Falha na leitura. Tente novamente.');}
  }
  async function start(){
    if(starting)return;stop();resetChoices();const id=epoch;starting=true;
    if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia){starting=false;say('A câmera ao vivo exige HTTPS e um navegador compatível. Você pode tirar uma foto ou digitar o código.');return;}
    say('Solicitando acesso à câmera…');
    try{
      const media=await navigator.mediaDevices.getUserMedia({audio:false,video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1080}}});
      if(id!==epoch||!dialog.open){media.getTracks().forEach(t=>t.stop());return;}
      stream=media;video.srcObject=media;preview.hidden=false;await video.play();if(id!==epoch)return;
      const track=media.getVideoTracks()[0];torch.hidden=!track?.getCapabilities?.().torch;torch.textContent='Lanterna';torch.setAttribute('aria-pressed','false');starting=false;say('Centralize as barras dentro da marcação.');scan(id);
    }catch(err){if(id!==epoch)return;stop();say(err.name==='NotAllowedError'?'Acesso à câmera negado. Libere a permissão no navegador ou use uma foto.':'Não foi possível abrir a câmera. Tente novamente ou use uma foto.');}
  }
  $('open-camera').onclick=()=>{if(!dialog.open){dialog.showModal();start();}};
  $('camera-close').onclick=close;
  dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
  dialog.addEventListener('close',stop);
  $('camera-restart').onclick=start;
  torch.onclick=async()=>{const track=stream?.getVideoTracks()[0];if(!track)return;try{await track.applyConstraints({advanced:[{torch:!light}]});light=!light;torch.setAttribute('aria-pressed',String(light));torch.textContent=light?'Desligar lanterna':'Lanterna';}catch(e){say('Lanterna não disponível neste aparelho.');}};
  $('camera-photo').onclick=()=>{stop();resetChoices();say('Selecione ou fotografe um código de barras.');file.value='';file.click();};
  file.addEventListener('change',async()=>{
    const photo=file.files?.[0];if(!photo||!dialog.open)return;stop();resetChoices();const id=epoch;say('Analisando foto…');let url;
    try{if(photo.size>25_000_000)throw Error('Foto muito grande. Tire outra foto.');url=URL.createObjectURL(photo);const img=new Image();img.src=url;await img.decode();if(id!==epoch)return;
      const blob=await frame(img,img.naturalWidth,img.naturalHeight,false);const codes=await decode(blob,id);if(id!==epoch||!dialog.open)return;
      if(codes.length===1)choose(codes[0]);else if(codes.length>1)showChoices(codes);else say('Código não encontrado na foto. Aproxime a câmera e tente novamente.');
    }catch(err){if(id===epoch)say(err.message||'Foto não reconhecida.');}finally{if(url)URL.revokeObjectURL(url);file.value='';}
  });
  window.addEventListener('pagehide',stop);
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&stream){stop();if(dialog.open)say('Câmera pausada. Toque em Ativar câmera para continuar.');}});
})();
