/* Color values observed in the user's authorized TV reference UI, 2026-10-16. */
const chartPaletteColors = ["#ffffff","#dbdbdb","#b8b8b8","#9c9c9c","#808080","#636363","#4a4a4a","#2e2e2e","#0f0f0f","#000000","#f23645","#ff9800","#ffeb3b","#4caf50","#089981","#00bcd4","#2962ff","#673ab7","#9c27b0","#e91e63","#fccbcd","#ffe0b2","#fff9c4","#c8e6c9","#ace5dc","#b2ebf2","#bbd9fb","#d1c4e9","#e1bee7","#f8bbd0","#faa1a4","#ffcc80","#fff59d","#a5d6a7","#70ccbd","#80deea","#90bff9","#b39ddb","#ce93d8","#f48fb1","#f77c80","#ffb74d","#fff176","#81c784","#42bda8","#4dd0e1","#5b9cf6","#9575cd","#ba68c8","#f06292","#f7525f","#ffa726","#ffee58","#66bb6a","#22ab94","#26c6da","#3179f5","#7e57c2","#ab47bc","#ec407a","#b22833","#f57c00","#fbc02d","#388e3c","#056656","#0097a7","#1848cc","#512da8","#7b1fa2","#c2185b","#801922","#e65100","#f57f17","#1b5e20","#00332a","#006064","#0c3299","#311b92","#4a148c","#880e4f"];
let chartColorPopup = null;
function closeChartColorPicker() { chartColorPopup?.remove(); chartColorPopup = null; }
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
  const alphaKey = ({backgroundColor:'backgroundAlpha',targetColor:'targetAlpha',stopColor:'stopAlpha',textColor:'textAlpha',fibSingleColor:'fibSingleAlpha'})[key] ||
    (object?.type === 'fib' && key === 'color' ? 'fibTrendAlpha' : 'alpha');
  const alpha = level?.alpha ?? (isEma ? Number(document.querySelector('#emaModal').dataset[emaAlphaField] ?? indicatorSettings[emaKey] ?? 1) : object?.[alphaKey]) ?? 1;
  popup.innerHTML = '<div class="chart-color-grid"></div><label class="custom-color-row">自定义颜色 <input aria-label="十六进制颜色" maxlength="7"><button type="button">应用</button></label>' +
    '<label class="color-alpha-row">不透明度<input aria-label="颜色不透明度" type="range" min="0" max="100"><input aria-label="不透明度百分比" type="number" min="0" max="100">%</label>';
  const custom = popup.querySelector('[aria-label="十六进制颜色"]');custom.value=input.value;
  const applyColor = color => {
    if(!/^#[0-9a-f]{6}$/i.test(color)){showToast('颜色格式为 #RRGGBB');return;}
    input.value=color;custom.value=color;input.dispatchEvent(new Event('change',{bubbles:true}));
    popup.querySelectorAll('[data-swatch]').forEach(button=>button.classList.toggle('selected',button.dataset.swatch===color.toLowerCase()));
  };
  chartPaletteColors.forEach(color=>{
    const button=document.createElement('button');button.type='button';button.title=color;button.setAttribute('aria-label',color);
    button.dataset.swatch=color;button.style.background=color;button.classList.toggle('selected',color===input.value.toLowerCase());
    button.onclick=()=>applyColor(color);popup.querySelector('.chart-color-grid').append(button);
  });
  popup.querySelector('.custom-color-row button').onclick=()=>applyColor(custom.value);
  custom.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();applyColor(custom.value);}};
  const range=popup.querySelector('input[type="range"]'),number=popup.querySelector('input[type="number"]');
  range.value=number.value=Math.round(alpha*100);
  const applyAlpha = control => {
    const value=Number(control.value);if(!Number.isFinite(value)||value<0||value>100)return;
    range.value=number.value=value;
    if(isEma) return; // EMA modal changes apply on confirmation below.
    if(!object)return;
    rememberObjects();if(level)level.alpha=value/100;else object[alphaKey]=value/100;
    savePrototypeState();renderWorkspace();
  };
  range.onchange=()=>applyAlpha(range);number.onchange=()=>applyAlpha(number);
  if(isEma){range.onchange=number.onchange=event=>{
    const value=Number(event.target.value);if(!Number.isFinite(value)||value<0||value>100)return;
    range.value=number.value=value;document.querySelector('#emaModal').dataset[emaAlphaField]=String(value/100);
    if(input.id==='emaBbFillColor')document.querySelector('#emaBbFillAlpha').value=value;
  };}
  document.body.append(popup);
  const rect=anchor.getBoundingClientRect();
  popup.style.left=clamp(rect.left,8,window.innerWidth-popup.offsetWidth-8)+'px';
  popup.style.top=clamp(rect.bottom+5,8,window.innerHeight-popup.offsetHeight-8)+'px';
};
document.addEventListener('click', event => {
  const input=event.target.closest('input[type="color"]');
  if(input&&!input.disabled){event.preventDefault();window.openChartColorPicker(input);}
  else if(chartColorPopup&&!chartColorPopup.contains(event.target))closeChartColorPicker();
},true);
document.addEventListener('keydown',event=>{
  if(event.key==='Escape' && chartColorPopup){event.preventDefault();event.stopPropagation();closeChartColorPicker();}
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
  const rect=document.querySelector('#quickWidth').getBoundingClientRect();document.body.append(popup);
  popup.style.left=clamp(rect.left,8,window.innerWidth-100)+'px';popup.style.top=clamp(rect.bottom+5,8,window.innerHeight-popup.offsetHeight-8)+'px';
};

