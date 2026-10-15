// Field Guide -> Figma variable sync (Plugin API; run via the Figma MCP use_figma tool).
// Prepend `const PLAN = <output of export_variables.py>;` then run. Idempotent:
// collections, variables and the effect style are matched by name and updated
// in place, so rerunning after a token change updates Figma without duplicates.
// Variables that exist in Figma but no longer in code are reported, not deleted.

async function upsertCollection(name) {
  const all = await figma.variables.getLocalVariableCollectionsAsync();
  let c = all.find(x => x.name === name);
  const created = !c;
  if (!c) c = figma.variables.createVariableCollection(name);
  if (c.modes[0].name !== "Value") c.renameMode(c.modes[0].modeId, "Value");
  return { c, created };
}

const existing = await figma.variables.getLocalVariablesAsync();
const byKey = new Map(existing.map(v => [v.variableCollectionId + "|" + v.name, v]));
function upsertVar(col, name, type) {
  let v = byKey.get(col.id + "|" + name);
  if (v && v.resolvedType !== type) { v.remove(); v = null; }
  const created = !v;
  if (!v) { v = figma.variables.createVariable(name, col, type); byKey.set(col.id + "|" + name, v); }
  return { v, created };
}

const stats = { created: 0, updated: 0 };
const { c: prim } = await upsertCollection("_Primitives");
prim.hiddenFromPublishing = true;
const { c: sem } = await upsertCollection("Semantic");
const pMode = prim.modes[0].modeId, sMode = sem.modes[0].modeId;

// Primitives: base colors, then alpha steps, then dimensions in numeric order.
const order = k => k.startsWith("dimension/") ? 2e6 + Number(k.split("/")[1])
  : (k.split("/").length === 2 ? 0 : 1e6);
const primVars = {};
for (const name of Object.keys(PLAN.primitives).sort((a, b) => order(a) - order(b) || a.localeCompare(b))) {
  const p = PLAN.primitives[name];
  const { v, created } = upsertVar(prim, name, p.type);
  v.setValueForMode(pMode, p.value);
  v.scopes = [];
  if (p.code) v.setVariableCodeSyntax("WEB", p.code); else if (v.codeSyntax.WEB) v.removeVariableCodeSyntax("WEB");
  v.description = p.code ? "Palette. Use a Semantic variable instead." : "Primitive step referenced by Semantic variables.";
  primVars[name] = v; stats[created ? "created" : "updated"]++;
}

for (const s of PLAN.semantic) {
  const { v, created } = upsertVar(sem, s.name, s.type);
  v.setValueForMode(sMode, s.alias ? { type: "VARIABLE_ALIAS", id: primVars[s.alias].id } : s.value);
  v.scopes = s.scopes;
  v.setVariableCodeSyntax("WEB", s.code);
  stats[created ? "created" : "updated"]++;
}

// Shadows -> effect styles
const styles = await figma.getLocalEffectStylesAsync();
const effectIds = [];
for (const e of PLAN.effects) {
  let st = styles.find(x => x.name === e.name) || figma.createEffectStyle();
  st.name = e.name;
  st.description = `Code: ${e.code}. Menus and popovers only; resting UI is flat.`;
  st.effects = [{ type: "DROP_SHADOW", color: e.color, offset: { x: e.x, y: e.y }, radius: e.blur, spread: 0, visible: true, blendMode: "NORMAL" }];
  effectIds.push(st.id);
}

// Report Figma-only leftovers (not deleted)
const wanted = new Set([...Object.keys(PLAN.primitives).map(n => prim.id + "|" + n), ...PLAN.semantic.map(s => sem.id + "|" + s.name)]);
const orphans = (await figma.variables.getLocalVariablesAsync())
  .filter(v => (v.variableCollectionId === prim.id || v.variableCollectionId === sem.id) && !wanted.has(v.variableCollectionId + "|" + v.name))
  .map(v => v.name);

const check = await figma.variables.getVariableByIdAsync(byKey.get(sem.id + "|color/text/secondary").id);
return {
  collections: { _Primitives: { id: prim.id, vars: prim.variableIds.length, hidden: prim.hiddenFromPublishing },
                 Semantic: { id: sem.id, vars: sem.variableIds.length } },
  stats, effectIds, orphans,
  sample: { name: check.name, scopes: check.scopes, code: check.codeSyntax, value: check.valuesByMode[sMode] },
};
