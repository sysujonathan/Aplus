/* Quick controls describe existing drawing state; they never keep a second style copy. */
window.AplusDrawingQuickbar = (() => {
  const tools = {
    fib:['lineColor','width'], long:['textColor','targetColor','stopColor','order'],
    ray:['lineColor','textColor','width','dash'], trend:['lineColor','textColor','width','dash'],
    short:['textColor','targetColor','stopColor','order'],
    rectangle:['lineColor','backgroundColor','textColor','width','dash'],
    up:['lineColor','textColor'], down:['lineColor','textColor'],
    arrow:['fillColor','textColor'], path:['lineColor','width','dash'],
    channel:['lineColor','backgroundColor','textColor','width']
  };
  const alerts = new Set(['fib','long','ray','trend','short','rectangle','channel']);
  function controls(object) {
    // Keep compatibility-only text/ruler objects on their existing minimal bar.
    if(!tools[object.type])return ['lineColor',...(object.type==='text'?[]:['width']),'settings','delete'];
    const specific=tools[object.type];
    // Persisted pre-marker arrows remain line arrows, including their editable stroke.
    const legacy=object.type==='arrow'&&!object.arrow_marker_version?['lineColor','textColor','width','dash']:specific;
    return ['template',...legacy,'settings',...(alerts.has(object.type)?['alert']:[]),'lock','delete','more'];
  }
  const common = values => values.every(value=>value===values[0])?values[0]:null;
  function lines(object) {
    if(object.type==='channel')return channelLevels(object);
    if(object.type==='fib')return object.fibLevels || [];
    return [];
  }
  function value(object, control) {
    const color=object.color||'#2962ff';
    if(control==='lineColor') {
      if(object.type==='fib')return common([...(object.fibTrendLine?[color]:[]),...lines(object).map(level=>object.fibUseOneColor?object.fibSingleColor||color:level.color||color)]);
      if(object.type==='channel')return common(lines(object).map(level=>level.color||color));
      return color;
    }
    if(control==='width') {
      if(object.type==='fib')return common([...(object.fibTrendLine?[object.fibTrendWidth||object.width||1]:[]),object.fibHorizontalWidth||object.width||1]);
      if(object.type==='channel')return common(lines(object).map(level=>level.width||object.width||1));
      return object.width||2;
    }
    if(control==='dash')return object.dash||'solid';
    if(control==='fillColor')return color;
    if(control==='targetColor')return object.targetColor||'#089981';
    if(control==='stopColor')return object.stopColor||'#f23645';
    if(control==='textColor')return object.textColor||(['long','short'].includes(object.type)?'#ffffff':color);
    if(control==='backgroundColor')return object.backgroundColor||color;
    return null;
  }
  function alphaFor(object, key, level=null) {
    const global=object.alpha??1;
    if(level)return (object.type==='fib'&&object.fibUseOneColor?object.fibSingleAlpha??1:level.alpha??1)*global;
    if(key==='strokeAlpha')return object.strokeAlpha??global;
    if(key==='textAlpha')return (object.textAlpha??1)*(['long','short'].includes(object.type)?1:global);
    if(key==='backgroundAlpha')return (object.backgroundAlpha??(object.type==='channel'?.1:.13))*global;
    if(key==='targetAlpha'||key==='stopAlpha')return (object[key]??object.regionAlpha??.24)*global;
    return (object[key]??1)*global;
  }
  function opacity(object, control) {
    if(control==='lineColor'&&object.type==='fib')return common([
      ...(object.fibTrendLine?[alphaFor(object,'fibTrendAlpha')]:[]),...lines(object).map(level=>
        (object.fibUseOneColor?object.fibSingleAlpha??1:level.alpha??1)*(object.alpha??1))]);
    if(control==='lineColor'&&object.type==='channel')return common(lines(object).map(level=>alphaFor(object,'alpha',level)));
    return alphaFor(object,control==='lineColor'||control==='fillColor'?'strokeAlpha':
      ({textColor:'textAlpha',backgroundColor:'backgroundAlpha',targetColor:'targetAlpha',stopColor:'stopAlpha'})[control]);
  }
  function materializeOpacity(object) {
    // An explicit opacity edit splits the legacy shared multiplier without changing
    // its visible text/fills. Selecting or opening a bar never migrates a document.
    const global=object.alpha??1;
    if(object.type==='fib') {
      ensureFibSettings(object);materializeFibUniformColor(object);
      object.fibTrendAlpha=alphaFor(object,'fibTrendAlpha');
      object.fibLevels.forEach(level=>{level.alpha=(level.alpha??1)*global;});
    } else if(object.type==='channel') {
      ensureChannelLevels(object).forEach(level=>{level.alpha=(level.alpha??1)*global;});
    } else object.strokeAlpha=object.strokeAlpha??global;
    if(['rectangle','channel','path'].includes(object.type))object.backgroundAlpha=alphaFor(object,'backgroundAlpha');
    if(object.type==='rectangle')object.middleAlpha=(object.middleAlpha??1)*global;
    if(['long','short'].includes(object.type)) {
      object.strokeAlpha=object.strokeAlpha??global;
      object.targetAlpha=alphaFor(object,'targetAlpha');object.stopAlpha=alphaFor(object,'stopAlpha');
    }
    object.textAlpha=alphaFor(object,'textAlpha');object.alpha=1;
  }
  function apply(object, control, next, alpha=false) {
    if(alpha) {
      if(!Number.isFinite(next)||next<0||next>1)return false;
      materializeOpacity(object);
      if(control==='lineColor'&&object.type==='fib') {
        object.fibTrendAlpha=next;assignFibUniformAlpha(object,next);
      } else if(control==='lineColor'&&object.type==='channel')ensureChannelLevels(object).forEach(level=>{level.alpha=next;});
      else object[control==='lineColor'||control==='fillColor'?'strokeAlpha':
        ({textColor:'textAlpha',backgroundColor:'backgroundAlpha',targetColor:'targetAlpha',stopColor:'stopAlpha'})[control]]=next;
    } else if(control==='width') {
      if(!Number.isInteger(next)||next<1||next>8)return false;
      object.width=next;
      if(object.type==='fib')object.fibTrendWidth=object.fibHorizontalWidth=next;
      if(object.type==='channel')ensureChannelLevels(object).forEach(level=>{level.width=next;});
    } else if(control==='dash') {
      if(!['solid','dashed','dotted'].includes(next))return false;
      object.dash=next;
    } else {
      if(!/^#[0-9a-f]{6}$/i.test(next))return false;
      if(control==='lineColor'||control==='fillColor') {
        if(object.type==='fib'){ensureFibSettings(object);assignFibUniformColor(object,next);}
        if(object.type==='channel')ensureChannelLevels(object).forEach(level=>{level.color=next;});
        object.color=next;
      } else object[control]=next;
    }
    // Color/opacity in the fill picker is a real fill edit even when previously off.
    if(control==='backgroundColor')object.fill=true;
    return true;
  }
  return {tools,controls,value,opacity,alphaFor,materializeOpacity,apply};
})();

/* UI adapter: one delegated toolbar, existing palette and Store-backed history. */
const drawingQuickbar=window.AplusDrawingQuickbar;
const quickNames={template:'模板',lineColor:'划线工具颜色',fillColor:'划线工具背景',
  textColor:'文字颜色',backgroundColor:'背景颜色',targetColor:'利润背景颜色',stopColor:'止损背景颜色',
  width:'图形宽度',dash:'线条样式',order:'创建限价单',settings:'设置',alert:'添加绘图警报',lock:'锁定',delete:'移除',more:'更多'};
const quickUnavailable=new Set(['order','alert','more']);
const quickIcons={template:'<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="14" y="3" width="6" height="6" rx="1"/><rect x="3" y="14" width="6" height="6" rx="1"/><path d="M17 13v8m-4-4h8"/>',
  lineColor:'<path d="m5 15 10-11 4 4L9 19H5v-4Zm8-9 4 4"/>',
  fillColor:'<path d="m8 5 10 10-7 7L1 12l7-7Zm-1-3 7 7M4 10h12m5 5c-3 4-3 6 0 6s3-2 0-6Z"/>',
  textColor:'<path d="M5 5h14M12 5v14M8 19h8"/>',
  order:'<rect x="3" y="4" width="15" height="14" rx="1"/><path d="m6 8 3 3 4-3m-7 6 3-3 4 3m6 1v8m-4-4h8"/>'};
function quickIcon(control) {
  if(['backgroundColor','targetColor','stopColor'].includes(control))control='fillColor';
  const symbol={settings:'settings',alert:'alarm',lock:'lock',delete:'trash',more:'more'}[control];
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${symbol?`<use href="#i-${symbol}"/>`:quickIcons[control]||''}</svg>`;
}
function syncDrawingQuickbar(object) {
  const bar=document.querySelector('#selectedObject');
  if(!object){if(bar.dataset.objectId){window.closeChartColorPicker?.();bar.dataset.objectId='';}return;}
  const controls=drawingQuickbar.controls(object),signature=object.type+':'+controls.join(',');
  if(bar.dataset.objectId!==object.id){window.closeChartColorPicker?.();document.querySelector('#templateMenu').classList.remove('show');}
  if(bar.dataset.schema!==signature) {
    bar.querySelectorAll('[data-quick-control],.quick-group-divider').forEach(node=>node.remove());
    // Remove only the obsolete static controls; keep the drag handle and its binding.
    bar.querySelectorAll('#quickColor,#quickWidth,#quickSettings,#quickDelete,.quick-legacy-divider').forEach(node=>node.remove());
    controls.forEach(control=>{
      const button=document.createElement('button');button.type='button';button.dataset.quickControl=control;
      button.id=({lineColor:'quickColor',fillColor:'quickColor',width:'quickWidth',settings:'quickSettings',delete:'quickDelete',template:'quickTemplate'})[control]||'quick-'+control;
      const unavailable=quickUnavailable.has(control),title=quickNames[control];
      button.title=unavailable?`${title} · 功能暂未实现`:title;button.setAttribute('aria-label',button.title);
      if(unavailable){button.setAttribute('aria-disabled','true');button.classList.add('quick-unimplemented');}
      if(control==='width'){button.classList.add('quick-width');button.innerHTML='<i class="quick-width-preview"></i><span></span>';}
      if(control==='dash')button.innerHTML='<i class="quick-dash-preview"></i>';
      else if(control!=='width')button.innerHTML=quickIcon(control)+(control.endsWith('Color')?'<i class="quick-color-preview"></i>':'');
      if(unavailable)button.insertAdjacentHTML('beforeend','<small class="quick-unimplemented-mark" aria-hidden="true">#</small>');
      bar.append(button);
    });bar.dataset.schema=signature;
  }
  bar.dataset.objectId=object.id;
  controls.forEach(control=>{
    const button=bar.querySelector(`[data-quick-control="${control}"]`),value=drawingQuickbar.value(object,control);
    if(control==='width')button.querySelector('span').textContent=value===null?'— px':value+'px';
    if(control==='dash')button.querySelector('i').style.borderTopStyle=value;
    if(control.endsWith('Color')){const swatch=button.querySelector('.quick-color-preview');swatch.style.background=value||'';swatch.classList.toggle('mixed',value===null);}
    if(control==='lock'){button.classList.toggle('active',Boolean(object.locked));button.setAttribute('aria-pressed',String(Boolean(object.locked)));button.title=object.locked?'解锁':'锁定';button.setAttribute('aria-label',button.title);}
  });
}
function commitQuickDrawingStyle(object, control, value, alpha=false) {
  if(objectAtSelection()!==object)return;
  // Validate on a copy first: invalid edits must not consume an undo step.
  const draft=structuredClone(object);
  if(!drawingQuickbar.apply(draft,control,value,alpha)||JSON.stringify(draft)===JSON.stringify(object))return;
  rememberObjects();Object.assign(object,draft);savePrototypeState();renderWorkspace('overlay');
}
function openDrawingQuickChoice(control,anchor) {
  const object=objectAtSelection();if(!object)return;
  closeChartColorPicker();const popup=document.createElement('section');chartColorPopup=popup;
  popup.className='chart-stroke-picker quick-choice-menu';popup.setAttribute('aria-label',quickNames[control]);
  const choices=control==='width'?[1,2,3,4].map(n=>[n,n+'px']):[['solid','线形图'],['dashed','短虚线'],['dotted','点虚线']];
  choices.forEach(([value,label])=>{const button=document.createElement('button');button.type='button';button.setAttribute('aria-label',label);
    button.setAttribute('aria-pressed',String(drawingQuickbar.value(object,control)===value));
    const preview=document.createElement('i');preview.style.borderTopWidth=(control==='width'?value:1)+'px';preview.style.borderTopStyle=control==='dash'?value:'solid';
    button.append(preview,document.createTextNode(label));button.onclick=()=>{commitQuickDrawingStyle(object,control,value);closeChartColorPicker();};popup.append(button);});
  document.body.append(popup);positionChartPicker(popup,anchor);
}
document.querySelector('#selectedObject').addEventListener('click',event=>{
  const button=event.target.closest('[data-quick-control]');if(!button)return;
  event.stopPropagation();const control=button.dataset.quickControl,object=objectAtSelection();
  if(!object||quickUnavailable.has(control))return;
  if(control==='settings'){closeChartColorPicker();openObjectSettings();return;}
  if(control==='delete'){closeChartColorPicker();deleteSelectedDrawing();return;}
  if(control==='template'){closeChartColorPicker();openDrawingTemplateMenu(button);return;}
  if(control==='lock'){closeChartColorPicker();rememberObjects();object.locked=!object.locked;savePrototypeState();updateObjectTree();renderWorkspace('overlay');return;}
  if(control==='width'||control==='dash'){openDrawingQuickChoice(control,button);return;}
  const input=document.createElement('input');input.type='color';input.value=drawingQuickbar.value(object,control)||object.color||'#2962ff';
  input.onchange=()=>commitQuickDrawingStyle(object,control,input.value);
  window.openChartColorPicker(input,button,object,{
    alpha:()=>drawingQuickbar.opacity(object,control)??1,
    onAlphaChange:value=>commitQuickDrawingStyle(object,control,value,true)
  });
});
