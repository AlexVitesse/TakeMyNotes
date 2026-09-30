// Check de la lógica pura de ui/index.html: evalúa el <script> real con un DOM de mentira.
// Opcional (necesita node): node check_ui.js
const fs = require('fs'), path = require('path'), assert = require('assert');
const js = fs.readFileSync(path.join(__dirname, 'ui', 'index.html'), 'utf8')
             .match(/<script>([\s\S]*)<\/script>/)[1];

const els = {};                       // memorizado: $('#ta') devuelve siempre el mismo objeto
const el = s => els[s] || (els[s] = {
  textContent: '', innerHTML: '', className: '', value: '', placeholder: '', style: {},
  selectionStart: 0, selectionEnd: 0, scrollTop: 0, scrollHeight: 0,
  classList: { cls: new Set(), add(c) { this.cls.add(c); }, remove(c) { this.cls.delete(c); },
               contains(c) { return this.cls.has(c); } },
  focus() {},
});
const shown = s => el(s).classList.cls.has('show');
global.document = { querySelector: el, querySelectorAll: () => [], addEventListener() {},
                    documentElement: { dataset: {} } };
global.window = { addEventListener() {}, innerWidth: 1040, innerHeight: 660 };
global.getSelection = () => ({ selectAllChildren() {} });
let deleted = [], listed = [];         // listed: lo que hay en notas/ según el backend
global.pywebview = { api: {
  rename_session() {}, save_notes() {}, toggle_fav: async () => ({}),
  list_sessions: async () => listed,
  delete_session: async id => { if (id === 'enuso') return { ok: false, msg: 'en uso' };
                                deleted.push(id); return { ok: true }; },
  get_settings: async () => ({ keep_audio: false, label_speakers: true, theme: 'auto',
                               name: 'Eric', key_set: true }),
  delete_audio: async id => { deleted.push('wav:' + id); return { paths: { audio: '' } }; },
  resync: async id => { deleted.push('sync:' + id); return { ok: true }; },
  get_session: async id => ({ id, name: 'Vieja', turns: [], transcript: 'x', paths: {} }),
  is_dark: async () => true, notas_stamp: async () => 0, take_action: async () => '',
  search: async () => [], open_widget: async () => ({ ok: true, msg: 'Widget abierto ✓' }),
  ask: () => new Promise(r => { answer = r; }),
} };
let answer;                            // resuelve la pregunta en vuelo del chat
global.setInterval = () => {};

const api = new Function('return (function(){' + js + `
  return {highlight, turnRows, turnsOf, activeTurn, mmss, rename, md, newNote, del,
          confirmYes, closeConfirm, openSettings, jsarg, delAudio, itemHtml, poll, showWidget,
          resync, setQuery:q=>query=q, setCur:c=>cur=c, sess:s=>sessions=s, setMe:n=>myName=n,
          setTab:t=>tab=t, getCur:()=>cur, noteMarks, groupOf, ask, renderPane};})()`)();

// El token del buscador entra en un RegExp: no puede romperlo ni inyectar HTML
api.setQuery('a+b');
assert.strictEqual(api.highlight('a+b y a b'),
  '<mark>a</mark>+<mark>b</mark> y <mark>a</mark> <mark>b</mark>');
api.setQuery('(x');
assert.strictEqual(api.highlight('x'), '<mark>x</mark>');
api.setQuery('<img>');
assert.ok(!api.highlight('<img src=x>').includes('<img'), 'el html sigue escapado');
api.setQuery('reunión');                       // \w de JS es ASCII: hay que tokenizar con \p{L}
assert.strictEqual(api.highlight('la Reunión'), 'la <mark>Reunión</mark>');
api.setQuery('');
assert.strictEqual(api.highlight('nada'), 'nada');

// Tu nombre se marca distinto del hit del buscador, y en una sola pasada: un segundo replace
// sobre el HTML ya marcado le pegaría a los <mark> ("Mark" es un nombre válido)
api.setMe('Mark');
assert.strictEqual(api.highlight('avisale a Mark'), 'avisale a <mark class="me">Mark</mark>');
api.setQuery('avisale');
assert.strictEqual(api.highlight('avisale a Mark'),
  '<mark>avisale</mark> a <mark class="me">Mark</mark>');
api.setQuery('');
api.setMe('a+b');                              // el nombre también entra en un RegExp
assert.strictEqual(api.highlight('a+b'), '<mark class="me">a+b</mark>');
api.setMe('<img>');
assert.ok(!api.highlight('<img src=x>').includes('<img '), 'el html sigue escapado');
api.setMe('');
assert.strictEqual(api.highlight('nada'), 'nada');

// Transcripción estilo tl;dv: una fila por turno, con su minuto y clic para saltar el audio
assert.strictEqual(api.mmss(0), '00:00');
assert.strictEqual(api.mmss(754.6), '12:35');
assert.strictEqual(api.mmss(-3), '00:00');     // nunca un tiempo negativo en pantalla
api.setMe('Eric');
let h = api.turnRows({ turns: [
  { t: 0, who: 'Los demás', text: 'hola' },
  { t: 12.4, who: 'Eric', text: 'qué tal' },
  { t: 30, who: 'Yo', text: 'viejo pero mío' }] });
assert.ok(h.includes('data-t="12.4"') && h.includes('onclick="seek(12.4)"'), 'clic = salta ahí');
assert.ok(h.includes('>00:00<') && h.includes('>00:12<'), 'el minuto de cada turno');
assert.strictEqual((h.match(/class="turn mine"/g) || []).length, 2,
  'Eric y el "Yo" de las sesiones viejas son los dos míos');
assert.ok(/class="who ren"[^>]*>Los demás<\/div>/.test(h), 'la etiqueta, clickeable para renombrar');

// Sesión vieja: se guardó sin turns, así que se reconstruye del texto plano y va sin tiempos
const old = api.turnsOf({ transcript: 'Eric: hola\n\nLos demás: qué tal' });
assert.deepStrictEqual(old.map(x => [x.t, x.who]), [[null, 'Eric'], [null, 'Los demás']]);
h = api.turnRows({ transcript: 'Eric: hola' });
assert.ok(!h.includes('data-t') && !h.includes('seek('), 'sin tiempos no se puede saltar');
assert.strictEqual(api.turnsOf({ transcript: 'sin etiqueta ninguna' })[0].who, '');
h = api.turnRows({ turns: [{ t: 1, who: 'Los demás', text: 'mirá <img src=x> esto' }] });
assert.ok(h.includes('&lt;img') && !h.includes('<img '), 'la transcripción no inyecta html');

// El turno que suena es el último cuyo tiempo ya pasó, también al saltar la aguja hacia atrás
const rows = [0, 10, 20].map(t => ({ dataset: { t: String(t) } }));
assert.strictEqual(api.activeTurn(rows, 0), rows[0]);
assert.strictEqual(api.activeTurn(rows, 19.9), rows[1]);
assert.strictEqual(api.activeTurn(rows, 999), rows[2]);
assert.strictEqual(api.activeTurn(rows, 5), rows[0], 'volver atrás tiene que retroceder');
assert.strictEqual(api.activeTurn([{ dataset: { t: '5' } }], 1), null);   // antes del primero

// Una sesión que se está transcribiendo lleva el skeleton en la lista, para que se vea el proceso
api.setCur(null);
assert.ok(api.itemHtml({ id: 'x', status: 'pending' }).includes('skeleton'));
assert.ok(api.itemHtml({ id: 'x', status: 'done', stage: 'Resumiendo… 1/3' }).includes('skeleton'));
assert.ok(!api.itemHtml({ id: 'x', status: 'done' }).includes('skeleton'));

// Markdown del modelo: se escapa primero y se renderiza el subconjunto que usa
assert.strictEqual(api.md('**a** y *b*'), '<strong>a</strong> y <em>b</em>');
assert.ok(api.md('### T\n- x\n- y').includes('<ul><li>x</li><li>y</li></ul>'));
assert.strictEqual(api.md('1. uno\n2. dos'), '<ol><li value="1">uno</li><li value="2">dos</li></ol>');
// la numeración sobrevive a una línea suelta en el medio (la "Discusión:" de la minuta)
assert.ok(api.md('1. a\n   Discusión: x\n2. b').includes('<ol><li value="2">b</li></ol>'));
assert.ok(api.md('usa `x` acá').includes('<code>x</code>'));
assert.ok(api.md('<img src=x>').includes('&lt;img'), 'no puede inyectar html');
// El acta: título de sección en mayúsculas, y los saltos sueltos se ven (white-space:normal)
assert.strictEqual(api.md('ACCIONES\n**Eric**\n- x'),
  '<h4>ACCIONES</h4><strong>Eric</strong><ul><li>x</li></ul>');
assert.ok(api.md('PROPUESTO (sin respuesta)\n- x').startsWith('<h4>PROPUESTO (sin respuesta)</h4>'));
assert.strictEqual(api.md('Eric: dev\nAna: cliente'), 'Eric: dev<br>Ana: cliente');

// Acciones con tilde: solo en ACCIONES, contadas igual que acciones() en Python (misma acta que
// el selftest), y el estado sale de s.done
const acta = 'QUIÉNES\n- Eric, convoca\nACCIONES\n**Eric**\n- mandar la **propuesta**\n' +
             '- llamar a Ana\n**SIN DUEÑO - ASIGNAR**\n- definir fecha\nMENCIONADO\n- nada';
h = api.md(acta, [1]);
assert.strictEqual((h.match(/type="checkbox"/g) || []).length, 3, 'una por acción, nada más');
assert.ok(h.includes('toggleAct(0)') && h.includes('toggleAct(2)') && !h.includes('toggleAct(3)'));
assert.ok(/class="act done"><input[^>]*toggleAct\(1\)[^>]*checked>llamar a Ana/.test(h), h);
assert.ok(!api.md(acta).includes('checkbox'), 'sin done (chat, minuta) no hay tildes');
// La minuta: los 7 títulos y los acuerdos numerados
const minuta = 'MINUTA DE REUNIÓN\n\nReunión: Junta\nFecha: 30/09/2026\n\nASISTENTES\n- Eric — dev\n\n' +
  'ORDEN DEL DÍA\n1. Precio\n2. Fecha\n\nDESARROLLO\n1. Precio\n   Discusión: x\n   Conclusión: y\n\n' +
  'ACUERDOS Y COMPROMISOS\n1. Mandar propuesta — Responsable: Eric — Fecha: 03/10/2026\n\n' +
  'TEMAS PENDIENTES\n- nada\n\nPRÓXIMA REUNIÓN\nNo se definió\n\nElaboró: TakeMyNotes';
h = api.md(minuta);
assert.strictEqual((h.match(/<h4>/g) || []).length, 7, h);
assert.ok(h.includes('<li value="1">Mandar propuesta — Responsable: Eric'));

// Notas con hora -> segundo de la sesión (el inicio es el id); fuera de la reunión no cuentan
assert.deepStrictEqual(api.noteMarks({ id: '20260930_101500', dur: 3600,
  notes: 'antes de todo\n\n— 10:20 —\nver precio\n\n— 10:25:30 —\n\n— 12:00 —\nfuera' }),
  [{ t: 300, who: 'Nota', text: 'ver precio', note: true }]);
assert.deepStrictEqual(api.noteMarks({ id: 'raro', notes: '— 10:20 —\nx' }), []);
h = api.turnRows({ id: '20260930_101500', dur: 3600, notes: '— 10:16 —\nojo acá',
  turns: [{ t: 0, who: 'Los demás', text: 'hola' }, { t: 120, who: 'Los demás', text: 'sigo' }] });
assert.ok(h.indexOf('ojo acá') > h.indexOf('hola') && h.indexOf('ojo acá') < h.indexOf('sigo'),
  'la nota va en su minuto');
assert.ok(h.includes('onclick="seek(60)"') && h.includes('turn note'));
assert.ok(/class="who ren"[^>]*renameWho\(&quot;Los demás&quot;\)/.test(h), 'la etiqueta se renombra');
// Grupos de la lista por fecha
const hoy = new Date(2026, 8, 30);
assert.strictEqual(api.groupOf('2026-09-30', hoy), 'Hoy');
assert.strictEqual(api.groupOf('2026-09-29', hoy), 'Ayer');
assert.strictEqual(api.groupOf('2026-09-25', hoy), 'Esta semana');
assert.strictEqual(api.groupOf('2026-08-02', hoy), 'Agosto');
assert.strictEqual(api.groupOf('2025-12-02', hoy), 'Diciembre 2025');
// Sin acta (falló el resumen): el punto se ve desde la lista
assert.ok(api.itemHtml({ id: 'x', status: 'done', nosum: true }).includes('class="nosum"'));
assert.ok(!api.itemHtml({ id: 'x', status: 'done' }).includes('class="nosum"'));

// El título editable se normaliza y se corta igual que el backend (80)
api.setCur({ id: 's1', name: 'Viejo' });
api.sess([{ id: 's1', name: 'Viejo' }]);
let e = { textContent: '  Junta\nde   ventas  ' };
api.rename(e);
assert.strictEqual(e.textContent, 'Junta de ventas');
e = { textContent: 'x'.repeat(120) };
api.rename(e);
assert.strictEqual(e.textContent.length, 80);
e = { textContent: '   ' };                    // vacío: conserva el nombre actual
api.rename(e);
assert.strictEqual(e.textContent, 'x'.repeat(80));

// Pestaña Info: las rutas de Windows viajan dentro de un onclick='fn("…")', así que tienen que
// sobrevivir a los backslashes y a un apóstrofo en el nombre de una carpeta.
assert.ok(!api.jsarg("C:\\O'Brien\\notas").includes("'"), 'el apóstrofo cerraría el atributo');
assert.strictEqual(JSON.parse(api.jsarg('C:\\a\\b').replace(/&quot;/g, '"')), 'C:\\a\\b');
assert.strictEqual(api.jsarg(''), '&quot;&quot;');   // sin ruta: literal vacío, no undefined

// "+ Nueva nota": una entrada con la hora por clic, sin pisar lo anterior
api.setCur({ id: 's1', name: 'x', notes: '' });
el('#ta').value = '';                          // lo crea si el script no lo tocó todavía
api.newNote();
assert.match(els['#ta'].value, /^— \d\d:\d\d —\n$/);
els['#ta'].value += 'lo que escribí';
api.newNote();
assert.ok(els['#ta'].value.includes('lo que escribí'), 'no puede borrar lo escrito');
assert.strictEqual(els['#ta'].value.match(/— \d\d:\d\d —/g).length, 2);
// Eliminar es irreversible: no puede llamar al backend hasta que se confirme
(async () => {
  api.setCur({ id: 's9', name: 'Junta importante' });
  api.del();
  assert.deepStrictEqual(deleted, [], 'del() no puede borrar nada por sí solo');
  assert.match(els['#ctitle'].textContent, /Junta importante/);
  assert.ok(shown('#cmask'), 'el modal de confirmación tiene que estar visible');
  api.closeConfirm();                          // cancelar deja la sesión en paz
  assert.ok(!shown('#cmask'));
  api.confirmYes();
  await new Promise(r => setTimeout(r, 0));
  assert.deepStrictEqual(deleted, [], 'cancelar tiene que desarmar el "sí"');
  api.del();
  api.confirmYes();
  await new Promise(r => setTimeout(r, 0));
  assert.deepStrictEqual(deleted, ['s9'], 'confirmar sí borra');

  // Si el backend no pudo borrar (archivo en uso), la sesión sigue seleccionada y se avisa
  api.setCur({ id: 'enuso', name: 'Bloqueada' });
  api.del(); api.confirmYes();
  await new Promise(r => setTimeout(r, 0));
  assert.strictEqual(api.getCur().id, 'enuso', 'no puede darla por borrada');
  assert.match(els['#toast'].textContent, /en uso/);

  // Borrar el audio también es irreversible: mismo modal, y no toca la transcripción
  deleted = [];
  api.setCur({ id: 's7', name: 'Con audio', paths: { audio: 'C:\\x\\s7.wav' } });
  api.delAudio();
  assert.deepStrictEqual(deleted, [], 'delAudio() no puede borrar sin confirmar');
  api.confirmYes();
  await new Promise(r => setTimeout(r, 0));
  assert.deepStrictEqual(deleted, ['wav:s7'], 'confirmar sí borra el .wav');

  // Sincronizar una sesión vieja pisa la transcripción: tiene que confirmarse, y si esa sesión
  // sí tenía hablantes separados el aviso tiene que decir que se pierden
  deleted = [];
  api.setCur({ id: 's9', name: 'Vieja', turns: [], transcript: 'Eric: hola' });
  api.resync();
  assert.deepStrictEqual(deleted, [], 'resync() no puede llamar al backend sin confirmar');
  assert.match(els['#ctext'].textContent, /separación de hablantes.*se pierde/);
  assert.strictEqual(els['#cyes'].textContent, 'Sincronizar');
  assert.ok(!els['#cyes'].className.includes('danger'), 'no borra nada: el botón no va en rojo');
  api.setCur({ id: 's9', name: 'Vieja plana', turns: [], transcript: 'sin etiquetas' });
  api.resync();
  assert.ok(!/se pierde/.test(els['#ctext'].textContent), 'sin hablantes no hay nada que perder');
  api.confirmYes();
  await new Promise(r => setTimeout(r, 0));
  assert.deepStrictEqual(deleted, ['sync:s9'], 'confirmar sí sincroniza');
  api.del();                                   // y el modal de eliminar vuelve a ser rojo
  assert.strictEqual(els['#cyes'].textContent, 'Eliminar');
  assert.ok(els['#cyes'].className.includes('danger'));
  api.closeConfirm();

  // El último tramo del resumen tiene que llegar al detalle abierto SIN cambiar de sesión: el
  // poll refresca la lista (que ya viene sin stage) y ahí la sesión sale de los pendientes, así
  // que si no se relee acá el spinner "Resumiendo… 3/3" se queda girando para siempre.
  api.setCur({ id: 's1', status: 'done', stage: 'Resumiendo… 3/3', paths: {} });
  api.sess([{ id: 's1', status: 'done', stage: 'Resumiendo… 3/3' }]);
  listed = [{ id: 's1', status: 'done' }];   // el backend ya borró el stage: se acabó el resumen
  await api.poll();
  assert.ok(!api.getCur().stage, 'el detalle se quedó con el stage viejo');
  assert.strictEqual(api.getCur().name, 'Vieja', 'y con los datos frescos del backend');

  // Escribiendo en las notas, el poll trae los datos nuevos pero NO repinta el detalle: el
  // innerHTML nuevo se llevaba el foco y el cursor a mitad de palabra
  api.setCur({ id: 's1', status: 'pending', stage: 'Transcribiendo… 1/3', notes: 'a medio escr', paths: {} });
  api.sess([{ id: 's1', status: 'pending', stage: 'Transcribiendo… 1/3' }]);
  listed = [{ id: 's1', status: 'pending', stage: 'Transcribiendo… 2/3' }];
  el('#ta').id = 'ta'; document.activeElement = els['#ta'];
  els['#detail'].innerHTML = 'NO TOCAR';
  await api.poll();
  assert.strictEqual(els['#detail'].innerHTML, 'NO TOCAR', 'repintó mientras escribías');
  assert.strictEqual(api.getCur().notes, 'a medio escr', 'y no te pisa lo que no se guardó aún');
  assert.strictEqual(api.getCur().name, 'Vieja', 'pero sí trae lo nuevo del backend');
  document.activeElement = null;

  // Chat: con una pregunta en vuelo el input se apaga y una segunda no sale
  api.setCur({ id: 's1', status: 'done', chat: [], paths: {} }); api.setTab('chat');
  api.renderPane();
  el('#cq').value = 'primera';
  const vuelo = api.ask();
  assert.ok(els['#chatbar'].innerHTML.includes('disabled') && els['#chatbar'].innerHTML.includes('Pensando'));
  el('#cq').value = 'segunda';
  await api.ask();                             // ignorada: no llama al backend otra vez
  answer({ ok: true, a: 'respuesta' });
  await vuelo;
  assert.deepStrictEqual(api.getCur().chat, [{ q: 'primera', a: 'respuesta' }]);
  assert.ok(!els['#chatbar'].innerHTML.includes('disabled'));

  // El widget se puede volver a invocar desde la ventana (su ✕ lo cierra del todo)
  await api.showWidget();
  assert.match(els['#toast'].textContent, /Widget abierto/);

  // Configuración: se abre y trae tu nombre desde el backend
  api.openSettings();
  assert.ok(shown('#mask'), 'el modal de configuración tiene que abrirse');
  await new Promise(r => setTimeout(r, 0));
  assert.strictEqual(els['#me'].value, 'Eric');
  console.log('check ui ok');
})();
