const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const PptxGenJS = require('pptxgenjs');
const root = path.resolve(__dirname, '..'), out = __dirname;
const read = p => JSON.parse(fs.readFileSync(path.join(root,p),'utf8'));
const metrics=read('reports/evaluation-v3/metrics.json');
const baseline=read('reports/evaluation/metrics.json');
const data=read('reports/dataset_summary.json');
const browser=read('reports/browser_acceptance.json');
const xml=fs.readFileSync(path.join(root,'reports/pitch_tests.xml'),'utf8');
const attrs=xml.match(/<testsuite\s([^>]+)>/)[1];
const tests=Object.fromEntries([...attrs.matchAll(/(\w+)="([^"]*)"/g)].map(m=>[m[1],m[2]]));
assert.equal(metrics.evaluation_kind,'regression');
assert.equal(metrics.cases,240);
assert.equal(data.total_records,1200);
assert(Object.values(data.cross_split_overlap).every(n=>n===0));
assert.equal(tests.failures,'0');assert.equal(tests.errors,'0');assert.equal(tests.skipped,'0');
assert(browser.checks.includes('three_decisions_saved')&&browser.page_errors.length===0);
const full=metrics.full.overall, previous=baseline.full.overall;
const pct=n=>(100*n).toFixed(1).replace('.',',')+'%';
const dec=n=>n.toFixed(2).replace('.',',');
const pptx=new PptxGenJS();
pptx.layout='LAYOUT_WIDE';pptx.author='SafeNet KZ';pptx.company='SafeNet KZ';
pptx.subject='Объяснимый анализ материалов для ручной проверки';
pptx.title='SafeNet KZ — питч';pptx.lang='ru-RU';
pptx.theme={headFontFace:'Segoe UI',bodyFontFace:'Segoe UI',lang:'ru-RU'};
const C = { bg: '101B29', panel: '192738', muted: 'B3C4D5', white: 'F3F7FA', aqua: '66E6C1', blue: '71B8FF', amber: 'F3BB74', violet: 'B6A8FF', line: '35485C', red: 'FFA6A6' };
const shape = pptx.ShapeType;
let s;
const meta = [];
const htmlSlides = [];
let html = [];
const esc = value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
function box(x,y,w,h) { return `left:${x*96}px;top:${y*96}px;width:${w*96}px;height:${h*96}px;`; }
function rect(x,y,w,h,color=C.panel,line=color,r=0) {
  s.addShape(r ? shape.roundRect : shape.rect, {x,y,w,h,rectRadius:0.14,fill:{color},line:{color:line,width:0.8},radius:0.14});
  html.push(`<div class="shape" style="${box(x,y,w,h)}background:#${color};border:1px solid #${line};border-radius:${r?8:0}px"></div>`);
}
function line(x1,y1,x2,y2,color=C.line,width=1.4,dash) {
  const options={x:Math.min(x1,x2),y:Math.min(y1,y2),w:Math.abs(x2-x1),h:Math.abs(y2-y1),line:{color,width}};
  if (dash) options.line.dashType='dash';
  if ((x2-x1)*(y2-y1)<0) options.flipV=true;
  s.addShape(shape.line,options);
  html.push(`<svg class="lines" viewBox="0 0 1280 720"><line x1="${x1*96}" y1="${y1*96}" x2="${x2*96}" y2="${y2*96}" stroke="#${color}" stroke-width="${width*1.333}" ${dash?'stroke-dasharray="6 5"':''}/></svg>`);
}
function t(text,x,y,w,h,size=22,color=C.white,bold=false,align='left') {
  s.addText(text,{x,y,w,h,fontFace:'Segoe UI',fontSize:size,color,bold,margin:0,breakLine:false,align,valign:'top',paraSpaceAfterPt:0,lang:'ru-RU',lineSpacingMultiple:1.08});
  html.push(`<div class="text" style="${box(x,y,w,h)}font-size:${size*96/72}px;color:#${color};font-weight:${bold?700:400};text-align:${align}">${esc(text)}</div>`);
}
function pill(label,x,y,w,color=C.aqua) {
  rect(x,y,w,0.38,C.panel,C.line,1); t(label,x+0.12,y+0.055,w-0.24,0.23,12,color,true);
}
function circle(x,y,d,color=C.aqua) {
  s.addShape(shape.ellipse,{x,y,w:d,h:d,fill:{color},line:{color,width:0}});
  html.push(`<div class="shape" style="${box(x,y,d,d)}background:#${color};border-radius:50%"></div>`);
}
function note(text,y=6.62,color=C.muted) { t(text,0.68,y,11.95,0.3,12,color); }
function start(kicker,title,source,timing,notes,backup=false) {
  if(html.length) htmlSlides.push(html.join(''));
  html=[];
  s=pptx.addSlide(); s.background={color:C.bg};
  const n=meta.length+1;
  rect(0,0,0.14,7.5,C.aqua);
  t('SAFENET KZ',0.68,0.27,3,0.28,12,C.aqua,true);
  if(backup) t('ПРИЛОЖЕНИЕ · ДЛЯ ВОПРОСОВ',8.5,0.27,4.1,0.28,11,C.muted,false,'right');
  t(kicker.toUpperCase(),0.68,0.86,11.8,0.25,12,C.aqua,true);
  t(title,0.68,1.22,12,0.8,title.length>58?28:32,C.white,true);
  line(0.68,7.03,12.65,7.03);
  t('SafeNet KZ · 25.10.2026',0.68,7.16,9.8,0.19,10,C.muted);
  t(String(n).padStart(2,'0'),12.08,7.12,0.56,0.26,12,C.aqua,true,'right');
  s.addNotes(`${backup?'Приложение':`Основной питч · ${timing} секунд`}\n\n${notes}\n\nОснование: ${source}`);
  meta.push({n,kicker,title,source,timing,notes,backup});
}


function picture(file,x,y,w,h) {
  const binary=fs.readFileSync(path.join(root,file));
  assert.equal(binary.toString('ascii',1,4),'PNG');
  const ratio=binary.readUInt32BE(16)/binary.readUInt32BE(20);
  const width=Math.min(w,h*ratio),height=width/ratio;
  x+=(w-width)/2;y+=(h-height)/2;
  const data='data:image/png;base64,'+binary.toString('base64');
  s.addImage({data,x,y,w:width,h:height});
  html.push(`<img alt="${esc(file)}" class="shape" src="${data}" style="${box(x,y,width,height)}">`);
}

start('Объяснимый анализ контента','SafeNet KZ','ТЗ, разделы 1–3; README.md',20,
'SafeNet KZ — рабочее пространство для аналитика. Оно помогает находить в публикациях и изображениях признаки платных розыгрышей, продвижения казино и инвестиционного привлечения. Вместо одной непрозрачной метки аналитик получает очередь, причины срабатывания и связи между материалами. Решение остаётся за человеком.');
t('От публикации\nк обоснованному\nрешению',.68,2.38,8.8,2.3,43,C.white,true);
t('Три трека · русский и казахский · текст и OCR',.74,5.2,9.4,.5,22,C.muted);
pill('РУССКИЙ',.74,6.1,1.7);pill('ҚАЗАҚША',2.62,6.1,1.7);
[[10.5,2.7,11.65,3.8],[10.5,2.7,9.8,4.85],[9.8,4.85,11.65,3.8],[11.65,3.8,11.4,5.8]].forEach(v=>line(...v,C.line,2));
[[10.5,2.7,.35],[11.65,3.8,.66],[9.8,4.85,.4],[11.4,5.8,.24]].forEach(([x,y,d])=>circle(x-d/2,y-d/2,d,C.aqua));

start('Проблема','Схемы меняют форму. Сигналы остаются.','ТЗ, разделы 3–4; SafeNet_KZ_Hackathon.pdf',25,
'Один и тот же механизм может появиться в тексте, транслите или картинке. При этом отдельное слово ещё ничего не доказывает: казино бывает темой новости, а конкурс может быть бесплатным. Поэтому мы рассматриваем сочетания признаков и их контекст по трём направлениям. Аналитик видит не только совпадение, но и основание приоритета.');
[['01','Розыгрыши','Оплата участия\n+ случайный выбор'],['02','Казино','Азартная игра\n+ продвижение'],['03','Инвестиции','Обещание дохода\n+ привлечение людей']].forEach((v,i)=>{
 const x=.7+i*4.08;t(v[0],x,2.6,1,.65,34,C.aqua,true);line(x,3.42,x+3.7,3.42);
 t(v[1],x,3.77,3.75,.55,26,C.white,true);t(v[2],x,4.72,3.8,1.1,23,C.muted);
});
note('Новости, отрицания и бесплатное участие учитываются при разборе контекста.');

start('Рабочий продукт','Одна очередь. Два языка.','reports/ui/kk-desktop.png; reports/browser_acceptance.json',30,
'Это снимок работающего интерфейса на казахском языке. Материалы сортируются по приоритету; фильтры позволяют сузить очередь по направлению, языку и состоянию. Кнопки Русский и Қазақша переключают меню, формы и объяснения. Оригинал публикации сохраняется без перевода. Граф и сводка вынесены в отдельные разделы. Интерфейс проверен и на узких экранах.');
picture('reports/ui/kk-desktop.png',.68,2.22,8.6,4.6);
t('Қазақша / Русский',9.65,2.65,3,.55,22,C.aqua,true);
t('Выбор языка\nсохраняется',9.65,3.35,2.9,.95,21,C.white);
line(9.65,4.65,12.6,4.65);
t('Очередь\nКарточка\nГраф\nСводка',9.65,5.03,2.9,1.4,21,C.muted);

start('Объяснения','Каждый балл можно проверить','config/calibrated.toml; tests/unit/test_detection.py',35,
'Покажу простой синтетический пример: «Казино. Жми ссылку». Движок находит игровой контекст, призыв и сочетание этих двух сигналов. Вклады тридцать, двадцать пять и двадцать дают семьдесят пять баллов. В карточке каждый вклад связан с точной цитатой. Если сообщение является новостью или содержит отрицание, контекст меняет результат. Это шкала приоритета, а не вероятность нарушения.');
rect(.7,2.45,8,1.04);t('«Казино. Жми ссылку.»',1,2.72,7.4,.5,27,C.white,true);
const evidence=[['Игровой контекст','Казино',30],['Призыв к действию','Жми',25],['Сочетание сигналов','Казино + Жми',20]];
evidence.forEach(([label,quote,value],i)=>{
 const y=3.93+i*.75;t(label,.76,y,4,.4,20,C.white);t(quote,4.8,y,2.8,.4,18,C.muted);t('+'+value,7.65,y,.9,.4,21,C.aqua,true,'right');line(.76,y+.55,8.65,y+.55);
});
t('75',9.4,2.65,3,1.5,83,C.aqua,true);t('баллов из 100',9.5,4.3,3,.5,22,C.white);t('Точная цитата\nдля каждого\nвклада',9.5,5.22,3,1.15,21,C.muted);
note('Синтетический пример без влияния графа. Балл не является вероятностью.');

start('Граф связей','Общий домен связывает два материала','reports/ui/pitch-graph.png; METHODOLOGY.md',35,
'Во вкладке графа видны связи материалов через общие домены и другие извлечённые сущности. На снимке — два синтетических материала с одним доменом. Аналитик может выбрать узел, увидеть соседей и вернуться к публикации. Прибавка к оценке ограничена пятнадцатью баллами. Уже усиленный результат не передаёт усиление дальше. Сама связь не доказывает общего владельца или нарушения.');
picture('reports/ui/pitch-graph.png',.68,2.24,8.7,4.62);
t('До +15',9.65,2.75,3,.8,35,C.aqua,true);t('Влияние связи\nограничено',9.65,3.68,2.95,.9,21,C.white);
line(9.65,4.9,12.6,4.9);t('Без каскадного\nусиления\n\nИстория контекста\nсохраняется',9.65,5.2,3,1.45,18,C.muted);

start('Текст внутри изображения','OCR сохраняет проверяемое основание','tests/fixtures/ocr/ru.png; tests/integration/test_ocr_real.py; METHODOLOGY.md',30,
'Для картинки сначала выполняется OCR с русской и казахской моделями. Затем применяется та же логика, что и к обычному тексту. На примере бесплатного конкурса важно не потерять слова «без оплаты»: они меняют смысл. Распознанные фрагменты связаны с координатами изображения. После обработки сохраняется обезличенная реконструкция. Если распознавание не удалось, система показывает ошибку или неполный анализ, а не нулевой риск.');
picture('tests/fixtures/ocr/ru.png',.72,2.47,11.9,2.03);
[['01','Распознать','OCR: RU / KK'],['02','Понять контекст','«Участие без оплаты»'],['03','Сохранить основание','Цитата и координаты']].forEach((v,i)=>{
 const x=.74+i*4.05;line(x,4.85,x+3.65,4.85);t(v[0],x,5.1,.55,.4,17,C.aqua,true);t(v[1],x,5.68,3.8,.5,21,C.white,true);t(v[2],x,6.32,3.8,.35,16,C.muted);
});

start('Измеренный прогресс','Казино: найдено 16 из 48 примеров','reports/evaluation/metrics.json; reports/evaluation-v3/metrics.json',35,
'После разбора ошибок на train и validation мы расширили распознавание продвижения и транслита, исправили потерю новостного контекста. На повторной проверке прежних двухсот сорока материалов казино обнаружено в шестнадцати из сорока восьми положительных примеров, раньше — ни в одном. Ложных срабатываний этого трека стало ноль вместо двух. Но тридцать два примера всё ещё пропущены. Это прогресс на синтетической регрессии, не доказательство точности на реальных данных.');
[['Полнота казино',pct(previous.tracks.casino.recall),pct(full.tracks.casino.recall)],['Ложные тревоги казино',String(previous.tracks.casino.fp),String(full.tracks.casino.fp)],['FPR всей очереди',pct(previous.queue.fpr),pct(full.queue.fpr)]].forEach((v,i)=>{
 const x=.7+i*4.08;t(v[0],x,2.67,3.8,.7,21,C.white,true);
 t(v[1],x,3.72,3.6,.8,35,C.muted);t('↓',x,4.61,.55,.6,24,C.muted);t(v[2],x,5.24,3.8,.85,40,C.aqua,true);
});
note('Повторный test · синтетика · 240 материалов · пороги 45/55 по validation.');

start('Проверяемый результат','Функции готовы. Качество имеет предел.','reports/evaluation-v3/metrics.json; reports/pitch_tests.xml',30,
'В таблице — текущие результаты трёх направлений. Лучше всего на этом наборе работает трек розыгрышей. У казино высокая доля верных срабатываний, но низкая полнота. Инвестиционное привлечение также требует улучшения. При этом инженерная основа проверена: триста девять тестов прошли, решения и версии сохраняются, ошибки обработки выделены отдельно. Тесты сервиса не подменяют качество выявления.');
const headings=[['Трек',.78,3.55],['Precision',5,2.1],['Recall',7.72,1.85],['F1',10.62,1.45]];
headings.forEach(([label,x,w])=>t(label,x,2.58,w,.4,18,C.muted,true));line(.74,3.15,12.6,3.15);
[['lottery','Розыгрыши'],['casino','Казино'],['investment','Инвестиции']].forEach(([key,label],i)=>{
 const v=full.tracks[key],y=3.55+i*.76;t(label,.78,y,4.15,.5,25,C.white,true);
 t(pct(v.precision),5,y,2.3,.5,25,C.aqua);t(pct(v.recall),7.72,y,2.3,.5,25,key==='casino'?C.amber:C.white);t(dec(v.f1),10.62,y,1.5,.5,25,C.white);line(.74,y+.58,12.6,y+.58);
});
t(`${tests.tests} тестов пройдено`,.78,6.25,5.6,.45,23,C.aqua,true);t('Синтетическая регрессия ≠ реальная точность',6.5,6.3,6.1,.4,16,C.muted);

start('Методика и границы','Следующий шаг — независимая проверка','reports/dataset_summary.json; ROADMAP.md; DEPLOYMENT.md',30,
'Для воспроизводимости создано тысяча двести синтетических материалов, из них четыреста изображений. Семьи сценариев, кампании и сущности разделены между train, validation и test без пересечений. Пороги выбираются только на validation. Старый test уже открыт, поэтому текущие числа называем регрессией. Следующий рубеж — независимый набор и пилот на согласованных обезличенных данных. Маскирование и языковые правила остаются эвристическими.');
[['720','train'],['240','validation'],['240','test']].forEach((v,i)=>{
 const x=.74+i*4.05;t(v[0],x,2.5,3.7,.95,53,C.aqua,true);t(v[1],x,3.64,3.7,.45,23,C.muted);line(x,4.37,x+3.65,4.37);
});
t('0 пересечений семей, кампаний и сущностей',.76,4.9,11.8,.6,29,C.white,true);
t('Новый независимый набор → согласованные критерии → пилот',.76,5.94,11.8,.65,24,C.muted);
note('Перенос на реальные данные ещё не подтверждён.');

start('Предложение для пилота','Проверить пользу вместе с аналитиками','ROADMAP.md; DEMO.md',30,
'Предлагаем следующий шаг: выбрать партнёра для ограниченного пилота и согласовать обезличенные материалы. До начала зафиксировать допустимые ложные тревоги, полноту и время ручной проверки. Затем сравнить работу аналитика с системой и без неё, собрать решения и оценить результат на новом наборе. У нас уже есть рабочий сайт, объяснения, граф и сохраняемая обратная связь. Теперь нужно проверить практическую пользу на данных партнёра.');
[['01','Согласовать данные','Обезличенные материалы\nи правила доступа'],['02','Зафиксировать успех','Полнота, ложные тревоги\nи время проверки'],['03','Провести пилот','Сравнение с ручной работой\nи независимая оценка']].forEach((v,i)=>{
 const x=.72+i*4.08;t(v[0],x,2.56,1,.65,32,C.aqua,true);line(x,3.38,x+3.7,3.38);t(v[1],x,3.78,3.8,.85,25,C.white,true);t(v[2],x,4.9,3.8,1.1,21,C.muted);
});
t('safenet-kz.vercel.app',.74,6.35,8,.45,25,C.aqua,true);t('Доступ через Vercel',9,6.45,3.6,.3,14,C.muted,false,'right');

start('Приложение · методика','Что именно означают цифры','reports/evaluation-v3/metrics.json; METHODOLOGY.md',0,
'Положительный прогноз — балл не ниже 45. Высокий приоритет — от 55. Пороги выбраны на validation; веса правил назначены вручную. Матрица ошибок имеет вид TN, FP / FN, TP. Для казино TP=16, FP=0, FN=32, TN=192. Нулевая доля ложных срабатываний на этой небольшой синтетической выборке не означает нулевую ошибку в реальном потоке. Повторная проверка прежнего test не является независимой оценкой.',true);
t('Казино: матрица ошибок',.74,2.48,6,.6,27,C.white,true);
[['TP',16],['FP',0],['FN',32],['TN',192]].forEach(([label,value],i)=>{
 const x=.75+(i%2)*3.1,y=3.4+Math.floor(i/2)*1.46;rect(x,y,2.86,1.22);t(label,x+.2,y+.15,.85,.3,15,C.muted);t(String(value),x+1.08,y+.23,1.5,.7,34,label==='FN'?C.amber:C.aqua,true,'right');
});
t('45 / 55',7.5,2.58,4.8,1,49,C.aqua,true);t('Средний / высокий приоритет',7.52,3.8,4.9,.6,22,C.white);
t('Веса — вручную\nПороги — validation\nTest — повторная регрессия\nГраф — максимум +15',7.52,4.85,4.9,1.7,21,C.muted);

start('Приложение · вопросы','Готовые ответы и границы продукта','README.md; ROADMAP.md; reports/PITCH_REVISION.md',0,
'Это взвешенные правила, допустимые по ТЗ, а не обученный классификатор. Принимаем текст, готовые транскрипты и изображения; живого ASR и сбора закрытых групп нет. История результатов и действий аналитика сохраняется, переанализ создаёт новую версию. Для пилота нужны согласованные данные и критерии качества. Основные источники чисел: reports/evaluation-v3/metrics.json, reports/pitch_tests.xml, reports/dataset_summary.json. Исходный первый test сохранён в reports/evaluation/metrics.json.',true);
[['Это обученная модель?','Взвешенные правила с точными цитатами.'],['Что принимается на вход?','Текст, готовые транскрипты, изображения.'],['Что остаётся после перезапуска?','Подготовка, результаты и решения аналитика.'],['Что пока не доказано?','Качество на реальном потоке и нагрузочная ёмкость.']].forEach(([q,a],i)=>{
 const y=2.5+i*.97;t(q,.76,y,5,.65,22,C.aqua,true);t(a,6.1,y,6.4,.72,21,C.white);if(i<3)line(.74,y+.78,12.6,y+.78);
});

assert.equal(meta.length,12);assert.equal(meta.filter(m=>!m.backup).reduce((sum,m)=>sum+m.timing,0),300);
htmlSlides.push(html.join(''));
fs.writeFileSync(path.join(out,'SafeNet_KZ_Защита.html'),`<!doctype html><html lang="ru"><meta charset="utf-8"><title>SafeNet KZ — питч</title><style>@page{size:13.333333in 7.5in;margin:0}*{box-sizing:border-box}html,body{margin:0;background:#101b29}body{font-family:'Segoe UI',sans-serif}.slide{position:relative;width:1280px;height:720px;background:#101b29;break-after:page;overflow:hidden;margin:0 auto}.slide:last-child{break-after:auto}.shape,.text{position:absolute}.text{white-space:pre-wrap;line-height:1.08;overflow:visible}.lines{position:absolute;inset:0;width:1280px;height:720px;pointer-events:none}@media screen{.slide{margin-bottom:20px;box-shadow:0 0 0 1px #35485c}}</style>${htmlSlides.map((content,i)=>`<section class="slide" aria-label="Слайд ${i+1}">${content}</section>`).join('')}</html>`,'utf8');
const clock=n=>`${Math.floor(n/60)}:${String(n%60).padStart(2,'0')}`;
let elapsed=0;
const speech=meta.filter(m=>!m.backup).map(m=>{const start=elapsed;elapsed+=m.timing;return `## ${m.n}. ${m.title} · ${clock(start)}–${clock(elapsed)}\n\n${m.notes}\n`;}).join('\n');
fs.writeFileSync(path.join(out,'Текст_выступления.md'),'# SafeNet KZ — питч на пять минут\n\n10 основных слайдов и 2 запасных. Плановый тайминг — 5:00; перед выступлением нужна репетиция. Все реплики также находятся в заметках PowerPoint.\n\n'+speech+'\n## Если показываете живое демо\n\nЗамените часть слайдов 3–5 демонстрацией, а не добавляйте её к пяти минутам. Заранее откройте сайт, войдите в Vercel и дождитесь запуска API. Покажите очередь, Русский / Қазақша, цитату и вклад признака, затем граф для материала с общим доменом. Для решения аналитика используйте отдельную синтетическую демобазу. При недоступной сети используйте снимки на слайдах.\n\n## Вопросы жюри\n\n'+meta.filter(m=>m.backup).map(m=>`### ${m.title}\n\n${m.notes}\n`).join('\n')+'\n## Не подменять показатели\n\n75 баллов — не вероятность. 309 тестов — не качество детектора. Ноль ложных тревог казино на синтетике — не гарантия для реального потока. Новая оценка того же test — регрессия. Распознанные связи не доказывают общего владельца.\n','utf8');
fs.writeFileSync(path.join(out,'Слайды_и_источники.json'),JSON.stringify(meta,null,2),'utf8');
fs.writeFileSync(path.join(out,'README.md'),'# Питч SafeNet KZ\n\nОбновлено 25.10.2026. 10 основных слайдов на пять минут и 2 приложения.\n\n- [PDF для показа](SafeNet_KZ_Защита.pdf)\n- [Редактируемый PowerPoint](SafeNet_KZ_Защита.pptx), текст и фигуры редактируются, снимки интерфейса встроены как изображения.\n- [Текст выступления](Текст_выступления.md), также в заметках слайдов.\n- [Обзор](Обзор_слайдов.png) и [источники утверждений](Слайды_и_источники.json).\n\nЧисла извлекаются сборщиком из машинных отчётов, первый test сохранён отдельно. Снимки показывают текущий локальный сайт на синтетических данных. Повторная оценка test не независима и не доказывает реальную точность.\n\nСборка: Node.js с установленным pptxgenjs → `node presentation/build.cjs`; Playwright и Sharp → `node presentation/render.cjs`. PDF и PNG рендерятся из синхронной HTML-версии. Нативный рендеринг PowerPoint и живая репетиция не выполнены.\n','utf8');
pptx.writeFile({fileName:path.join(out,'SafeNet_KZ_Защита.pptx')}).then(()=>console.log(JSON.stringify({slides:meta.length,mainSlides:10,seconds:300,tests:tests.tests,sourceAssertions:'passed'})));
