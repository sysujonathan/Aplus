/* Color values observed in the user's authorized TV reference UI, 2026-10-16. */
const chartPaletteColors = ["#ffffff","#dbdbdb","#b8b8b8","#9c9c9c","#808080","#636363","#4a4a4a","#2e2e2e","#0f0f0f","#000000","#f23645","#ff9800","#ffeb3b","#4caf50","#089981","#00bcd4","#2962ff","#673ab7","#9c27b0","#e91e63","#fccbcd","#ffe0b2","#fff9c4","#c8e6c9","#ace5dc","#b2ebf2","#bbd9fb","#d1c4e9","#e1bee7","#f8bbd0","#faa1a4","#ffcc80","#fff59d","#a5d6a7","#70ccbd","#80deea","#90bff9","#b39ddb","#ce93d8","#f48fb1","#f77c80","#ffb74d","#fff176","#81c784","#42bda8","#4dd0e1","#5b9cf6","#9575cd","#ba68c8","#f06292","#f7525f","#ffa726","#ffee58","#66bb6a","#22ab94","#26c6da","#3179f5","#7e57c2","#ab47bc","#ec407a","#b22833","#f57c00","#fbc02d","#388e3c","#056656","#0097a7","#1848cc","#512da8","#7b1fa2","#c2185b","#801922","#e65100","#f57f17","#1b5e20","#00332a","#006064","#0c3299","#311b92","#4a148c","#880e4f"];
let chartColorPopup = null;
let chartPopupAnchor = null;
const chartCustomColors = [];
function chartHexFromHsv(h,s,v) {
  const chroma=v*s,part=((h%360)+360)%360/60,x=chroma*(1-Math.abs(part%2-1)),base=v-chroma;
  const channels=part<1?[chroma,x,0]:part<2?[x,chroma,0]:part<3?[0,chroma,x]:part<4?[0,x,chroma]:part<5?[x,0,chroma]:[chroma,0,x];
  return '#'+channels.map(value=>Math.round((value+base)*255).toString(16).padStart(2,'0')).join('');
}
function chartHsvFromHex(hex) {
  const [r,g,b]=[1,3,5].map(index=>parseInt(hex.slice(index,index+2),16)/255),max=Math.max(r,g,b),min=Math.min(r,g,b),delta=max-min;
  const h=!delta?0:max===r?60*((g-b)/delta%6):max===g?60*((b-r)/delta+2):60*((r-g)/delta+4);
  return {h:(h+360)%360,s:max?delta/max:0,v:max};
}
function showChartCustomColorEditor(popup,input,accept) {
  const hsv=chartHsvFromHex(input.value);
  popup.innerHTML='<div class="custom-color-header"><i></i><input aria-label="十六进制颜色" maxlength="7"><button type="button">增加</button></div><div class="custom-color-controls"><div class="custom-sv-plane" role="slider" tabindex="0" aria-label="饱和度和亮度"><i></i></div><input type="range" min="0" max="360" aria-label="色相"></div>';
  const hex=popup.querySelector('[aria-label="十六进制颜色"]'),plane=popup.querySelector('.custom-sv-plane'),hue=popup.querySelector('[aria-label="色相"]');
  const refresh=()=>{
    const color=chartHexFromHsv(hsv.h,hsv.s,hsv.v);hex.value=color;
    popup.querySelector('.custom-color-header i').style.background=color;hue.value=hsv.h;
    plane.style.backgroundColor=chartHexFromHsv(hsv.h,1,1);
    plane.querySelector('i').style.left=(hsv.s*100)+'%';plane.querySelector('i').style.top=((1-hsv.v)*100)+'%';
    plane.setAttribute('aria-valuetext',`饱和度 ${Math.round(hsv.s*100)}%，亮度 ${Math.round(hsv.v*100)}%`);
  };
  const point=event=>{const rect=plane.getBoundingClientRect();hsv.s=clamp((event.clientX-rect.left)/rect.width,0,1);hsv.v=1-clamp((event.clientY-rect.top)/rect.height,0,1);refresh();};
  plane.onpointerdown=event=>{plane.setPointerCapture(event.pointerId);point(event);};
  plane.onpointermove=event=>{if(plane.hasPointerCapture(event.pointerId))point(event);};
  plane.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;event.preventDefault();event.stopPropagation();
    hsv.s=clamp(hsv.s+(event.key==='ArrowLeft'?-.01:event.key==='ArrowRight'?.01:0),0,1);hsv.v=clamp(hsv.v+(event.key==='ArrowDown'?-.01:event.key==='ArrowUp'?.01:0),0,1);refresh();};
  hue.oninput=()=>{hsv.h=Number(hue.value);refresh();};
  const add=()=>{
    const color='#'+hex.value.replace(/^#/,'').toLowerCase();if(!/^#[0-9a-f]{6}$/.test(color)){showToast('颜色格式为 #RRGGBB');return;}
    if(!chartCustomColors.includes(color))chartCustomColors.push(color);accept(color);
  };
  popup.querySelector('button').onclick=add;
  hex.oninput=()=>{const color='#'+hex.value.replace(/^#/,'');if(/^#[0-9a-f]{6}$/i.test(color)){Object.assign(hsv,chartHsvFromHex(color));refresh();}};
  hex.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();add();}};
  refresh();
}
function closeChartColorPicker() { chartColorPopup?.remove(); chartColorPopup = null; chartPopupAnchor = null; }
function appendChartStrokeChoices(popup, object, key, closeOnChoice = false) {
  const width = key==='width'||key.endsWith('Width');
  const group = document.createElement('div');group.className = 'stroke-choice-group';
  group.setAttribute('role','group');group.setAttribute('aria-label',width?'粗细':'线型');
  const choices = width ? [1,2,3,4].map(n=>[n,n+' px']) : [['solid','线形图'],['dashed','短虚线'],['dotted','点虚线']];
  choices.forEach(([value,label])=>{
    const button = document.createElement('button');button.type='button';button.setAttribute('aria-label',label);
    button.setAttribute('aria-pressed',String((object[key] ?? (width?object.width||1:key==='fibTrendDash'?'dashed':object.dash||'solid'))===value));
    const line=document.createElement('i');line.className='stroke-preview-line';
    line.style.borderTopWidth=(width?value:1)+'px';line.style.borderTopStyle=width?'solid':value;button.append(line);
    if(!width){const text=document.createElement('span');text.textContent=label;button.append(text);}
    button.onclick=()=>{
      rememberObjects();object[key]=value;savePrototypeState();renderWorkspace();refreshFibStylePreviews(objectAtSelection());
      group.querySelectorAll('button').forEach(choice=>choice.setAttribute('aria-pressed',String(choice===button)));
      if(closeOnChoice)closeChartColorPicker();
    };
    group.append(button);
  });
  if(!closeOnChoice){const label=document.createElement('p');label.className='stroke-choice-label';label.textContent=width?'厚度':'线条样式';popup.append(label);}
  popup.append(group);
}
function positionChartPicker(popup, anchor) {
  chartPopupAnchor=anchor;
  const rect=anchor.getBoundingClientRect();
  popup.style.left=clamp(rect.left,8,Math.max(8,window.innerWidth-popup.offsetWidth-8))+'px';
  popup.style.top=clamp(rect.bottom+5,8,Math.max(8,window.innerHeight-popup.offsetHeight-8))+'px';
}
window.openChartStrokePicker = (object,key,anchor) => {
  closeChartColorPicker();const popup=document.createElement('section');chartColorPopup=popup;
  popup.className='chart-stroke-picker';popup.setAttribute('aria-label',key.endsWith('Width')?'水平线粗细':'水平线线型');
  appendChartStrokeChoices(popup,object,key,true);document.body.append(popup);positionChartPicker(popup,anchor);
};
window.openChartColorPicker = (input, anchor = input, objectOverride = null) => {
  closeChartColorPicker();
  const popup = document.createElement('section');
  chartColorPopup = popup; popup.className = 'chart-color-picker'; popup.setAttribute('aria-label','颜色面板');
  const object = objectOverride || (document.querySelector('#objectModal').classList.contains('show') ? objectAtSelection() : null);
  const level = object && input.dataset.fibLevelColor !== undefined ? object.fibLevels[Number(input.dataset.fibLevelColor)] :
    object && input.dataset.channelField==='color' ? object.channelLevels[Number(input.closest('[data-channel-index]').dataset.channelIndex)] : null;
  const emaKey=({emaColor:'ema_alpha',emaSmoothColor:'ema_smooth_alpha',emaBbUpperColor:'ema_bb_upper_alpha',emaBbLowerColor:'ema_bb_lower_alpha',emaBbFillColor:'ema_bb_fill_alpha'})[input.id];
  const isEma=Boolean(emaKey),emaAlphaField=input.id==='emaColor'?'alpha':input.id+'Alpha';
  const key = input.dataset.setting || input.dataset.fibSetting || 'color';
  const alphaKey = ({backgroundColor:'backgroundAlpha',middleColor:'middleAlpha',targetColor:'targetAlpha',stopColor:'stopAlpha',textColor:'textAlpha',fibSingleColor:'fibSingleAlpha'})[key] ||
    (object?.type === 'fib' && key === 'color' ? 'fibTrendAlpha' : 'alpha');
  const alpha = (level && object.type==='fib' && object.fibUseOneColor ? object.fibSingleAlpha ?? 1 : level?.alpha) ?? (isEma ? Number(document.querySelector('#emaModal').dataset[emaAlphaField] ?? indicatorSettings[emaKey] ?? 1) : object?.[alphaKey]) ?? (key==='backgroundColor'?(object?.type==='channel'?.1:.13):1);
  popup.innerHTML = '<div class="chart-color-grid"></div><div class="chart-custom-swatches"></div><button type="button" class="custom-color-add" aria-label="添加自定义颜色">＋</button>' +
    '<label class="color-alpha-row">不透明度<input aria-label="颜色不透明度" type="range" min="0" max="100"><input aria-label="不透明度百分比" type="number" min="0" max="100">%</label>';
  const applyColor = color => {
    if(!/^#[0-9a-f]{6}$/i.test(color)){showToast('颜色格式为 #RRGGBB');return;}
    input.value=color;input.dispatchEvent(new Event('change',{bubbles:true}));
    popup.querySelectorAll('[data-swatch]').forEach(button=>button.classList.toggle('selected',button.dataset.swatch===color.toLowerCase()));
  };
  [...chartPaletteColors,...chartCustomColors].forEach((color,index)=>{
    const button=document.createElement('button');button.type='button';button.title=color;button.setAttribute('aria-label',color);
    button.dataset.swatch=color;button.style.background=color;button.classList.toggle('selected',color===input.value.toLowerCase());
    button.onclick=()=>applyColor(color);popup.querySelector(index<chartPaletteColors.length?'.chart-color-grid':'.chart-custom-swatches').append(button);
  });
  popup.querySelector('.custom-color-add').onclick=()=>{showChartCustomColorEditor(popup,input,color=>{applyColor(color);window.openChartColorPicker(input,anchor,objectOverride);});positionChartPicker(popup,anchor);};
  const range=popup.querySelector('input[type="range"]'),number=popup.querySelector('input[type="number"]');
  range.value=number.value=Math.round(alpha*100);
  const applyAlpha = control => {
    if(control.value.trim()==='')return;
    const value=Number(control.value);if(!Number.isFinite(value)||value<0||value>100)return;
    range.value=number.value=value;
    if(isEma) return; // EMA modal changes apply on confirmation below.
    if(!object)return;
    const previous=level && object.type==='fib' && object.fibUseOneColor ? object.fibSingleAlpha ?? 1 : level?.alpha ?? object[alphaKey] ?? 1;
    if(previous===value/100 && !(object.type==='fib' && key==='fibSingleColor' && object.fibLevels.some(item=>(item.alpha??1)!==value/100)))return;
    rememberObjects();
    if(object.type==='fib' && key==='fibSingleColor')assignFibUniformAlpha(object,value/100);
    else if(level){if(object.type==='fib')materializeFibUniformColor(object);level.alpha=value/100;}
    else object[alphaKey]=value/100;
    savePrototypeState();renderWorkspace();
  };
  range.onchange=()=>applyAlpha(range);number.oninput=number.onchange=()=>applyAlpha(number);
  if(isEma){range.onchange=number.oninput=number.onchange=event=>{
    const value=Number(event.target.value);if(!Number.isFinite(value)||value<0||value>100)return;
    range.value=number.value=value;document.querySelector('#emaModal').dataset[emaAlphaField]=String(value/100);
    if(input.id==='emaBbFillColor')document.querySelector('#emaBbFillAlpha').value=value;
  };}
  const strokeObject=input.dataset.channelField==='color'?level:object;
  if(strokeObject && input.dataset.strokeWidth)appendChartStrokeChoices(popup,strokeObject,input.dataset.strokeWidth);
  if(strokeObject && input.dataset.strokeDash)appendChartStrokeChoices(popup,strokeObject,input.dataset.strokeDash);
  document.body.append(popup);positionChartPicker(popup,anchor);
};
document.addEventListener('click', event => {
  const input=event.target.closest('input[type="color"]');
  if(input&&!input.disabled){event.preventDefault();window.openChartColorPicker(input);}
  else if(chartColorPopup&&!chartColorPopup.contains(event.target))closeChartColorPicker();
},true);
document.addEventListener('keydown',event=>{
  if(event.key==='Escape' && chartColorPopup){event.preventDefault();event.stopPropagation();closeChartColorPicker();}
});
window.addEventListener('resize',()=>{
  if(!chartColorPopup||!chartPopupAnchor)return;
  if(!chartPopupAnchor.isConnected){closeChartColorPicker();return;}
  positionChartPicker(chartColorPopup,chartPopupAnchor);
});
window.openChartWidthPicker = () => {
  const object=objectAtSelection();if(!object)return;
  closeChartColorPicker();
  const popup=document.createElement('section');chartColorPopup=popup;popup.className='chart-width-picker';
  [1,2,3,4,5,6,7,8].forEach(width=>{
    const button=document.createElement('button');button.type='button';button.textContent=width+' px';
    button.onclick=()=>{rememberObjects();object.width=width;document.querySelector('#quickWidth').textContent=width+'px';savePrototypeState();renderWorkspace();closeChartColorPicker();};
    popup.append(button);
  });
  document.body.append(popup);positionChartPicker(popup,document.querySelector('#quickWidth'));
};

