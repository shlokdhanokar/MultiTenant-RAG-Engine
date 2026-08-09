import { chromium } from 'playwright';

const URL = 'https://multi-tenant-rag-engine.vercel.app';
const SP = process.argv[2];

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', e => errors.push(String(e)));

await page.goto(URL, { waitUntil: 'networkidle' });

// 1. The workspace, not the landing screen, is what loads.
const landingGone = !(await page.getByText('Test RAG on a demo knowledge base').count());
const hasTabs = await page.getByRole('button', { name: 'Chat', exact: true }).count() > 0;
console.log('landing screen gone :', landingGone);
console.log('workspace loaded    :', hasTabs);

// 2. The "add your own" entry is present in the knowledge-base list.
const addBtn = page.getByRole('button', { name: /Add your own knowledge base/i });
console.log('add-your-own visible:', await addBtn.isVisible());

// 3. Run demo.
await page.screenshot({ path: `${SP}/shot-1-workspace.png` });
const runDemo = page.getByRole('button', { name: /Run demo/i });
console.log('run demo visible    :', await runDemo.isVisible());
await runDemo.click();

// A user turn should appear immediately, then an answer with metrics.
await page.waitForSelector('.msg.user', { timeout: 15000 });
const asked = (await page.locator('.msg.user .bubble').first().innerText()).trim();
console.log('auto-asked          :', JSON.stringify(asked));

await page.waitForSelector('.msg.ai .metrics', { timeout: 180000 });
const answer = (await page.locator('.msg.ai .bubble').first().innerText()).trim();
const metrics = (await page.locator('.msg.ai .metrics').first().innerText()).replace(/\s+/g, ' ');
console.log('answer len          :', answer.length);
console.log('answer head         :', answer.slice(0, 90).replace(/\n/g, ' '));
console.log('metrics             :', metrics);
console.log('kb selected         :', (await page.locator('.tenant.active .tenant-name').innerText()).trim());
console.log('inspector populated :', await page.locator('.cand').count(), 'candidates');
await page.screenshot({ path: `${SP}/shot-2-demo-ran.png` });

// 4. The add-your-own entry routes to Upload.
await addBtn.click();
await page.waitForTimeout(600);
console.log('add -> upload tab   :', await page.locator('.dropzone').isVisible());
await page.screenshot({ path: `${SP}/shot-3-upload.png` });

console.log('console errors      :', errors.length ? errors : 'none');
await browser.close();
