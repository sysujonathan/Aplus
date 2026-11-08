/* Workspace owns shared controls. Each isolated renderer owns its editor. */
(() => {
  const workspace = chartBoot.workspace;
  if (!workspace) return;
  document.body.classList.add('shared-workspace');
  document.body.classList.toggle('workspace-multi',workspace.multi === true);
  document.body.classList.toggle('workspace-inactive',workspace.active === false);
  let pending = 0, pointer = null, applying = false;
  window.aplusWorkspaceSaveStarted = () => { pending++; };
  window.aplusWorkspaceSaveFinished = () => { pending = Math.max(0, pending - 1); };
  const scope = kind => ({kind, layout:workspace.layout, pane:workspace.pane});
  const ensureScopes = () => userObjects.forEach(object => {
    if (!object.shareScope && !object._legacyScope) object.shareScope = scope(workspace.defaultScope || 'pane');
  });
  // Legacy objects are globally visible, explicitly marked so new tool defaults
  // cannot silently reassign their ownership on the next ordinary edit.
  userObjects.forEach(object => { if (!object.shareScope) object.shareScope = {kind:'global'}; });
  window.aplusWorkspaceBeforeSave = ensureScopes;
  window.aplusWorkspaceConfirmDelete = objects => {
    const shared=objects.filter(object=>(object.shareScope?.kind || 'global')!=='pane');
    return !shared.length || window.confirm(`将删除 ${shared.length} 个共享图形，其他引用它们的图格也会移除。可用撤销恢复。继续？`);
  };
  const selected = document.querySelector('#selectedObject');
  const picker = document.createElement('select');
  picker.title = '绘图共享范围；缩小范围会保留共享原件并生成独立副本';
  picker.setAttribute('aria-label','绘图共享范围');
  for (const [value, text] of [['','共享范围…'],['pane','本图'],['layout','当前布局'],['global','全部布局']]) {
    const option = document.createElement('option'); option.value=value; option.textContent=text; picker.append(option);
  }
  selected.append(picker);
  picker.addEventListener('change', () => {
    const object = objectAtSelection(), target = picker.value; picker.value='';
    if (!object || !target) return;
    const old = object.shareScope?.kind || 'global';
    if (old === target) return;
    const rank={pane:0,layout:1,global:2};
    if (rank[target] < rank[old] && !window.confirm('保留共享原图形，并在所选范围创建独立副本？')) return;
    rememberObjects();
    if (rank[target] < rank[old]) {
      const copy = structuredClone(object); copy.id=crypto.randomUUID(); copy.shareScope=scope(target);
      userObjects.push(copy); selectedId=copy.id;
    } else object.shareScope=scope(target);
    savePrototypeState(); updateObjectTree(); renderWorkspace();
  });
  const actions = {undo:'undoAction',redo:'redoAction',alert:'openAlert',replay:'startReplay',
    snapshot:'snapshot',objects:'togglePanel'};
  let comparison=null;
  const comparisonLabel=document.createElement('button');
  comparisonLabel.style.cssText='position:absolute;left:8px;top:75px;z-index:7;background:#161b22;color:#e6edf3;border:1px solid #30363d;border-radius:4px;padding:4px 7px;max-width:90%;font-size:11px';
  comparisonLabel.hidden=true; document.querySelector('.chart-wrap').append(comparisonLabel);
  comparisonLabel.onclick=()=>{comparison=null;comparisonLabel.hidden=true;renderWorkspace();};
  const originalRender=renderWorkspace;
  renderWorkspace=function(...args) {
    originalRender(...args);
    if (!comparison || !canvas._view) return;
    const source=comparison.series[activeTimeframeUnit];
    comparisonLabel.textContent=`比较 ${comparison.symbol} · ${source?'可见区间涨跌幅 %':'当前周期无同源行情'} · ×移除`;
    if (!source) return;
    const data=new Map(source.map(row=>[row.date,row.close])),view=canvas._view;
    const rows=view.rows.filter(row=>data.has(row.date));
    if (rows.length<2) return;
    const baseline=data.get(rows[0].date),primary=rows[0].close,values=rows.map(row=>({row,pct:(data.get(row.date)/baseline-1)*100}));
    const ctx=canvas.getContext('2d');ctx.save();ctx.beginPath();ctx.rect(view.left,view.top,view.plotWidth,view.plotHeight);ctx.clip();ctx.strokeStyle='#bda0ff';ctx.lineWidth=1.5;ctx.beginPath();
    values.forEach(({row,pct},n)=>{
      const x=view.xGlobal(bars.findIndex(item=>item.date===row.date)),y=view.y(primary*(1+pct/100));
      n?ctx.lineTo(x,y):ctx.moveTo(x,y);
    });ctx.stroke();ctx.restore();
    comparisonLabel.textContent=`比较 ${comparison.symbol} ${values.at(-1).pct.toFixed(2)}% · 首个共同可见日归零 · ×移除`;
  };
  window.aplusWorkspaceCommand = command => {
    if (closingWorkspace) return false;
    const {kind,value}=command;
    if (kind==='active') {
      document.body.classList.toggle('workspace-inactive',value!==true);
      return true;
    }
    if(kind==='compare') {
      if(!value?.series || value.source!==chartBoot.source || value.adjustment!==chartBoot.adjustment)return false;
      comparison=value;comparisonLabel.hidden=false;renderWorkspace();return true;
    }
    if (kind==='tool') {
      if (value==='smile') return startTool(value) === true;
      if (!['cross','cursor-dot','cursor-arrow','cursor-highlight','cursor-eraser','zoom','zoomOut','magnet','lock','text',...Object.keys(toolNames)].includes(value)) return false;
      startTool(value, document.querySelector(`[data-tool="${value}"]`) || document.querySelector(`[data-favorite="${value}"]`)); return true;
    }
    if(kind==='visibility') {
      if(value!=null && !['drawings','indicators','strategy','all'].includes(value))return false;
      toggleChartVisibility(value ?? indicatorSettings.hide_scope);return true;
    }
    if(kind==='magnetMode') {
      if(!['weak','off'].includes(value))return false;
      if(magnetEnabled!==(value==='weak'))startTool('magnet',document.querySelector('[data-tool="magnet"]'));
      return true;
    }
    if(kind==='removeLocked') {
      if(typeof value!=='boolean')return false;
      indicatorSettings.remove_locked=value;savePrototypeState();return true;
    }
    if(kind==='remove') {
      if(!['drawings','indicators','all'].includes(value))return false;
      removeChartObjects(value);return true;
    }
    if (kind==='timeframe') {
      if (activeTimeframeUnit===value) return true;
      return switchProductionTimeframe(value);
    }
    if (kind==='chartType') {
      if (!['candles','line'].includes(value)) return false;
      setChartType(value);return true;
    }
    if (kind==='defaultScope') { if (!['pane','layout','global'].includes(value)) return false; workspace.defaultScope=value; return true; }
    if (kind==='action') {
      if (value==='snapshotSave') {
        downloadChartSnapshot();return true;
      }
      if (value==='snapshotLink' || value==='snapshotOpen') {
        if (!window.pywebview?.api?.local_snapshot) return false;
        localChartSnapshot(value==='snapshotLink'?'link':'open');return true;
      }
      if(value==='settings') {
        if (document.querySelector('#workspaceChartSettings')) return false;
        const dialog=document.createElement('dialog');dialog.style.cssText='background:#161b22;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:20px';
        dialog.id='workspaceChartSettings';
        dialog.innerHTML='<h3>活动图设置</h3><p><label><input type="checkbox" data-grid="horizontal_grid"> 横向网格线</label></p><p><label><input type="checkbox" data-grid="vertical_grid"> 纵向网格线</label></p><button data-close>关闭</button>';
        dialog.querySelectorAll('[data-grid]').forEach(input=>{
          const key=input.dataset.grid;
          input.checked=indicatorSettings[key]!==false;
          input.onchange=()=>{
            rememberObjects();
            indicatorSettings[key]=input.checked;
            savePrototypeState();
            renderWorkspace();
          };
        });
        dialog.querySelector('[data-close]').onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();return true;
      }
      const element = value==='indicators' ? document.querySelector('[title="技术指标"]') :
        document.getElementById(actions[value]);
      if (!element) return false; element.click(); return true;
    }
    if (kind==='rangePreset') {
      const button=[...document.querySelectorAll('.rangebar nav button')].find(b=>b.textContent===value);
      if (!button) return false;button.click();return true;
    }
    if (kind==='sync') {
      if (replayActive || activeDrawingTool || pending || !value || applying) return false;
      applying=true;
      try {
        if (value.timeframe && activeTimeframeUnit!==value.timeframe && !switchProductionTimeframe(value.timeframe,false)) return false;
        const index = day => chartCoordinates.indexForDate(bars,day,activeTimeframeUnit);
        if (Array.isArray(value.range)) {
          const a=index(value.range[0]), b=index(value.range[1]);
          if (a>=0 && b>=a) { viewCount=Math.max(1,b-a+1); viewOffset=bars.length-1-b; }
        } else if (value.time) {
          const b=index(value.time); if (b>=0) viewOffset=bars.length-1-b;
        }
        renderWorkspace();
        if ('crosshair' in value) {
          const p=value.crosshair, i=p?index(p.date):-1;
          if (i<0 || !canvas._view) crosshair.classList.remove('show');
          else {
            const samePrice=p.identity===chartBoot.identity&&p.basis===priceBasis();
            const x=canvas._view.xGlobal(i), y=p.price && samePrice ? canvas._view.y(p.price) : canvas._view.top;
            crosshair.style.setProperty('--cx',`${x}px`); crosshair.style.setProperty('--cy',`${y}px`);
            document.querySelector('#crossDate').textContent=p.date;
            document.querySelector('#crossPrice').textContent=samePrice?chartPrice(p.price):'';
            crosshair.classList.add('show');
          }
        }
        return true;
      } finally { applying=false; }
    }
    return false;
  };
  const priceBasis=()=>/^m\d+$/.test(activeTimeframeUnit)?String(chartBoot.live?.adjustment||'不复权'):String(chartBoot.adjustment);
  canvas.addEventListener('mousemove', event => {
    if (!canvas._view) return;
    const rect=canvas.getBoundingClientRect(), view=canvas._view;
    const index=Math.floor(view.begin+(event.clientX-rect.left-view.left)/view.candleWidth);
    const row=bars[index];
    const y=event.clientY-rect.top;
    const price=view.high-(y-view.top)/(view.plotHeight||1)*(view.high-view.low);
    pointer=row&&y>=view.top&&y<=view.height-view.bottom?{date:row.date,price,identity:chartBoot.identity,basis:priceBasis()}:null;
  });
  canvas.addEventListener('mouseleave',()=>{pointer=null;});
  window.aplusWorkspaceState = () => {
    const end=Math.max(0,bars.length-1-viewOffset), begin=Math.max(0,end-viewCount+1);
    const scope=indicatorSettings.hide_scope;
    const key=({drawings:'drawings_visible',indicators:'indicators_visible',strategy:'strategy_visible'})[scope];
    const hidden=scope==='all'?!drawingsVisible&&indicatorSettings.indicators_visible===false&&indicatorSettings.strategy_visible===false:
      indicatorSettings[key]===false;
    return {identity:chartBoot.identity,symbol:chartBoot.symbol,timeframe:activeTimeframeUnit,
      chartType:indicatorSettings.chart_type==='line'?'line':'candles',
      asof:bars.at(-1)?.date,tool:activeDrawingTool||(typeof cursorMode==='undefined'||cursorMode==='cross'?'cross':`cursor-${cursorMode}`),magnet:magnetEnabled,
      locked:Boolean(indicatorSettings.lock_drawings),hidden,
      removeLocked:Boolean(indicatorSettings.remove_locked),
      drawingCount:userObjects.length,indicatorCount:indicatorSettings.ema?1:0,
      available:Object.keys(chartBoot.series||{}),
      time:bars[Math.min(end,bars.length-1)]?.date,range:[bars[begin]?.date,bars[Math.min(end,bars.length-1)]?.date],
      crosshair:pointer,dirty:pending>0||bridgeSaveFailed,replay:replayActive,
      saveError:bridgeSaveFailed?document.querySelector('.save-state').textContent:''};
  };
  window.aplusWorkspaceDrawings = (objects, revision) => {
    if (pending || bridgeSaveFailed || activeDrawingTool || closingWorkspace) return false;
    const incoming=structuredClone(objects);
    incoming.forEach(object=>chartCoordinates.projectObject(object,bars,activeTimeframeUnit));
    // Remote edits are not added as local undo gestures. Rebase unchanged
    // objects in each gesture snapshot so undo cannot delete another pane's work.
    const before=new Map(userObjects.map(object=>[object.id,JSON.stringify(object)]));
    const after=new Map(incoming.map(object=>[object.id,object]));
    for (const stack of [undoStack,redoStack]) for (const snapshot of stack) {
      for (let i=snapshot.length-1;i>=0;i--) {
        const object=snapshot[i];
        if (JSON.stringify(object)!==before.get(object.id)) continue;
        if (after.has(object.id)) snapshot[i]=structuredClone(after.get(object.id)); else snapshot.splice(i,1);
      }
      for (const object of incoming) if (!before.has(object.id)) snapshot.push(structuredClone(object));
    }
    userObjects.splice(0,userObjects.length,...incoming);
    if (!incoming.some(object=>object.id===selectedId)) selectedId=null;
    bridgeRevision=Number(revision); updateObjectTree(); renderWorkspace(); return true;
  };
})();
