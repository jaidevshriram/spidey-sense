#!/usr/bin/env node
// Turn the hackathon-slide-deck build output into the final deck at docs/deck/:
// dark chrome, Spidey fonts, images served from img/, and a speaker-notes panel toggled with "n".
//
// Usage: node postprocess.mjs <built-dir> <deck-dir>
import { readFileSync, writeFileSync, mkdirSync, readdirSync, copyFileSync } from "node:fs";
import { join } from "node:path";

const [BUILT, DECK] = process.argv.slice(2);
let html = readFileSync(join(BUILT, "index.html"), "utf8");

const rep = (from, to) => {
  if (!html.includes(from)) throw new Error("postprocess: pattern not found: " + from);
  html = html.split(from).join(to);
};

rep('family=Baloo+2:wght@500;600;700;800&display=swap',
    'family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500;700&display=swap');
rep("body { background: #EEF2F6; font-family: 'Baloo 2', system-ui, sans-serif; }",
    "body { background: #07080C; font-family: 'Inter', system-ui, sans-serif; }");
rep("background: #F7F9FB; border-right: 1px solid #DCE3EA;", "background: #0E1118; border-right: 1px solid #1E2431;");
rep("background: #C6D0DA; border-radius: 4px;", "background: #2C3444; border-radius: 4px;");
rep("border-radius: 8px; background: #fff; cursor: pointer;", "border-radius: 8px; background: #0B0D12; cursor: pointer;");
rep(".thumb:hover { border-color: #9FC6EC; }", ".thumb:hover { border-color: #5C6375; }");
rep(".thumb.on { border-color: #1D6FD1; box-shadow: 0 2px 8px rgba(29,111,209,.3); }",
    ".thumb.on { border-color: #FF4D5E; box-shadow: 0 2px 10px rgba(255,77,94,.35); }");
rep(".stage { flex: 1; position: relative; overflow: hidden; background: #E7ECF2; }",
    ".col { flex: 1; display: flex; flex-direction: column; min-width: 0; }\n" +
    "  .stage { flex: 1; position: relative; overflow: hidden; background: #07080C; }");
rep("background: rgba(255,255,255,.86); box-shadow: 0 2px 10px rgba(20,32,46,.2);",
    "background: rgba(30,36,49,.9); box-shadow: 0 2px 10px rgba(0,0,0,.4);");
rep("font-size: 21px; font-weight: 800; color: #1D6FD1; line-height: 1;", "font-size: 21px; font-weight: 800; color: #FF4D5E; line-height: 1;");
rep("font-size: 13px; font-weight: 700; color: #5A6B7C;\n    background: rgba(255,255,255,.86);",
    "font-size: 13px; font-weight: 700; color: #8C95A8;\n    background: rgba(30,36,49,.9);");

// speaker notes: hidden inside slides, shown in a panel under the stage
const notesCss = `
  .slide .notes { display: none !important; }
  .notes-panel { display: none; height: 150px; flex-shrink: 0; overflow-y: auto; background: #0E1118;
    border-top: 2px solid #FF4D5E; color: #E4E8F0; font-size: 19px; line-height: 1.5; padding: 16px 28px; }
  .notes-panel .lbl { font-family: 'JetBrains Mono', monospace; font-size: 12px; letter-spacing: .14em; color: #FF4D5E; margin-bottom: 6px; }
  body.show-notes .notes-panel { display: block; }
  .hint { position: absolute; left: 16px; bottom: 14px; z-index: 10; font-size: 12px; color: #5C6375; font-family: 'JetBrains Mono', monospace; }
  @media print { .notes-panel, .hint { display: none !important; } }
`;
rep("  @media (max-width: 820px)", notesCss + "\n  @media (max-width: 820px)");
rep('<main class="stage">', '<div class="col">\n  <main class="stage">');
rep('<div class="counter"></div>\n  </main>',
    '<div class="counter"></div>\n    <div class="hint">← → navigate · n notes</div>\n  </main>\n  <div class="notes-panel" aria-live="polite"></div>\n  </div>');

const notesJs = `
<script>
(function () {
  var panel = document.querySelector('.notes-panel');
  function update() {
    var a = document.querySelector('.stage > .slide.active');
    var n = a && a.querySelector('.notes');
    panel.innerHTML = '<div class="lbl">SPEAKER NOTES \\u00b7 SLIDE ' + (a ? a.id : '') + '</div>' + (n ? n.innerHTML : '');
  }
  new MutationObserver(update).observe(document.querySelector('.stage'), { subtree: true, attributes: true, attributeFilter: ['class'] });
  addEventListener('keydown', function (e) {
    if ((e.key === 'n' || e.key === 'N') && !e.metaKey && !e.ctrlKey) {
      document.body.classList.toggle('show-notes');
      update();
      dispatchEvent(new Event('resize'));
    }
  });
  update();
})();
</script>
</body>`;
rep("</body>", notesJs);

rep('src="assets/', 'src="img/');

writeFileSync(join(DECK, "index.html"), html);
mkdirSync(join(DECK, "img"), { recursive: true });
let n = 0;
for (const f of readdirSync(join(BUILT, "assets"))) { copyFileSync(join(BUILT, "assets", f), join(DECK, "img", f)); n++; }
console.log(`wrote ${join(DECK, "index.html")} + ${n} images in img/`);
