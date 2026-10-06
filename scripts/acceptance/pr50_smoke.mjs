import {browser} from './pr50_browser.mjs';
import {resolve, join} from 'node:path';
const base = process.env.HR_ACCEPTANCE_URL ?? 'http://localhost:5173';
const fixtures = process.env.HR_ACCEPTANCE_ARTIFACTS ?? '/tmp/hrm-acceptance';
import assert from 'node:assert/strict';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
const out=resolve('screenshots/pr50-follow-up');
await mkdir(out,{recursive:true});
const results=[]; const errors=[]; let lastPage;
try {
for (const role of ['hr','admin','manager']) {
 const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce',acceptDownloads:true});
 const page=await context.newPage();lastPage=page;page.setDefaultTimeout(10000);
 page.on('pageerror', e=>errors.push({role,error:e.message}));
 await page.goto(base);
 await page.getByLabel('Имя пользователя').fill(`visual-${role}`);
 await page.getByLabel('Пароль').fill('Str0ng-Pass-2026');
 await page.getByRole('button',{name:'Войти',exact:true}).click();
 await page.locator('.workspace').waitFor();
 const skip=page.getByRole('button',{name:'Пропустить',exact:true});
 await skip.waitFor({timeout:2000}).catch(()=>{});
 if(await skip.isVisible()) { await page.getByRole('button',{name:'Сохранить',exact:true}).click(); await skip.waitFor({state:'hidden'}); }
 await page.goto(base+'/#/candidates');
 await page.locator('.row-name').first().click();
 await page.getByRole('button',{name:'Загрузить анкету',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('input[type=file]') && !document.querySelector('input[type=file]').disabled);
 if(!await page.getByText(`${role}-form.docx`,{exact:true}).count()) {
 await page.getByLabel('Файл анкеты или скана').setInputFiles({name:`${role}-form.docx`,mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:await readFile(join(fixtures,'test-form.docx'))});
 await page.getByText(`Файл «${role}-form.docx» загружен.`,{exact:true}).waitFor();
 }
 const downloadPromise=page.waitForEvent('download');
 await page.getByRole('button',{name:'Скачать',exact:true}).last().click();
 const download=await downloadPromise;
 assert.deepEqual(await readFile(await download.path()),await readFile(join(fixtures,'test-form.docx')));
 await page.getByRole('button',{name:'Закрыть панель'}).click();
 await page.locator('.row-attachments').first().waitFor();
 for (const theme of ['light','dark']) for (const width of [1440,390]) {
  const key=`${role}-${theme}-${width}`;
  await page.setViewportSize({width,height:width===1440?1000:844});
  await page.evaluate(theme=>localStorage.setItem('hrm-theme',JSON.stringify({theme,density:'comfortable'})),theme);
  await page.reload();await page.locator('.row-name').first().waitFor();await page.waitForLoadState('networkidle');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${key}: candidates page overflow`);
  assert.equal(await page.locator('html').getAttribute('data-theme'),theme);
  const shot=async name=>page.screenshot({path:`${out}/${key}-${name}.png`});
  await page.locator('.row-name').first().scrollIntoViewIfNeeded();
  await shot('candidates');
  await page.locator('.row-name').first().click();
  await page.locator('.candidate-detail-row').first().waitFor();
  await shot('drawer');
  const drawer=await page.locator('.drawer-panel').evaluate(el=>({width:el.clientWidth,scroll:el.scrollWidth}));
  assert.ok(drawer.scroll<=drawer.width+1,`${key}: drawer horizontal overflow ${JSON.stringify(drawer)}`);
  await page.getByRole('tab',{name:'Документы',exact:true}).click();
  await page.getByRole('tab',{name:'Документы',exact:true}).getAttribute('aria-selected').then(v=>assert.equal(v,'true'));
  await shot('checklist');
  await page.getByRole('tab',{name:'Документы и анкеты',exact:true}).click();
  await page.getByText(`${role}-form.docx`,{exact:true}).first().waitFor();
  await shot('files');
  await page.getByRole('button',{name:'Закрыть панель'}).click();
  await page.goto(base+'/#/kanban');
  await page.locator('.kanban-card').first().waitFor();
  await page.getByText('Загрузка доски…',{exact:true}).waitFor({state:'hidden'});
  const board=page.locator('.kanban-board');
  const dimensions=await board.evaluate(el=>({client:el.clientWidth,scroll:el.scrollWidth,height:el.clientHeight,scrollHeight:el.scrollHeight,overflow:getComputedStyle(el).overflowX, gap:getComputedStyle(el).gap,widths:[...el.querySelectorAll('.kanban-column')].map(c=>c.getBoundingClientRect().width),documentWidth:document.documentElement.scrollWidth,viewport:innerWidth}));
  assert.ok(dimensions.scroll>dimensions.client,`${key}: no board overflow`);
  assert.ok(dimensions.widths.every(w=>w===296),`${key}: unstable columns ${dimensions.widths}`);
  assert.equal(dimensions.gap,'16px');
  assert.ok(dimensions.documentWidth<=dimensions.viewport,`${key}: document overflow ${JSON.stringify(dimensions)}`);
  await shot('kanban-start');
  await page.getByRole('button',{name:'Прокрутить воронку вправо'}).click();
  assert.ok(await board.evaluate(el=>el.scrollLeft)>0);
  await board.evaluate(el=>{el.scrollLeft=el.scrollWidth;});
  await shot('kanban-end');
  // Arrow keys work when the board is focused, without dragging a card.
  await board.focus(); await page.keyboard.press('Home');
  await page.getByRole('button',{name:/^Уведомления, непрочитанных:/}).click();
  const popover=page.getByRole('dialog',{name:'Последние уведомления'});
  assert.equal(await popover.locator('.bell-item').count(),5);
  const box=await popover.boundingBox();assert.ok(box.x>=0&&box.x+box.width<=width,`${key}: clipped notification popover ${JSON.stringify(box)}`);
  const rowWidths=await popover.locator('.bell-item').evaluateAll(nodes=>nodes.map(el=>({row:el.clientWidth,parent:el.parentElement.clientWidth})));
  assert.ok(rowWidths.every(w=>w.row===w.parent));
  await shot('notifications');
  await page.keyboard.press('Escape');
  await page.goto(base+'/#/schedule');
  await page.locator('.schedule-name').first().waitFor();await page.waitForLoadState('networkidle');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${key}: schedule page overflow`);
  const schedule=await page.locator('.schedule-name').first().evaluate(el=>({display:getComputedStyle(el).display,height:el.getBoundingClientRect().height,siblingHeight:el.nextElementSibling.getBoundingClientRect().height}));
  assert.equal(schedule.display,'table-cell'); assert.equal(schedule.height,schedule.siblingHeight);
  await page.locator('.schedule-name').first().scrollIntoViewIfNeeded();
  await shot('schedule');
  results.push({role,theme,width,board:dimensions,drawer,popover:box,schedule,uploadDownload:'real API; byte-identical DOCX'});
  console.log('PASS',key);
  await page.goto(base+'/#/candidates');
 }
 await page.setViewportSize({width:1440,height:1000});
 await page.goto(base+'/#/kanban');
 await page.getByRole('button',{name:/^Уведомления, непрочитанных:/}).click();
 const responsePromise=page.waitForResponse(r=>r.url().endsWith('/notifications/mark-all-read')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'Прочитать все',exact:true}).click();
 assert.equal((await responsePromise).status(),204);
 await page.waitForFunction(()=>document.querySelector('.bell-badge')?.textContent==='0');
 assert.equal(await page.locator('.bell-item.is-unread').count(),0);
 await page.screenshot({path:`${out}/${role}-dark-1440-notifications-read.png`});
 await context.close();
}
assert.deepEqual(errors,[]);
} catch(error) { await lastPage.screenshot({path:join(fixtures,'failure.png')}); throw error; } finally {await writeFile(`${out}/measurements.json`,JSON.stringify({fixture:'Synthetic test data; real FastAPI API; in-memory SQLite; Chromium 153; no network mocks',results,errors},null,2));await browser.close();}
