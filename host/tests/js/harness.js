// Minimal DOM stub: enough to exercise content.js's decision logic.
"use strict";

class FakeNode { constructor(tag){ this.tagName=tag.toUpperCase(); this.children=[]; this.nodeType=1; } }
class FakeText { constructor(t){ this.textContent=t; this.nodeType=3; } }

function makeEl(tag, text, childEls) {
  const el = new FakeNode(tag);
  el.children = childEls || [];
  el._text = text || "";
  el.nodeType = 1;
  el.classList = { add(){}, };
  el.dataset = {};
  Object.defineProperty(el, "textContent", {
    get(){ return this._text; },
    set(v){ this._text = v; this.children = []; },
  });
  el.replaceChildren = function(node){ this._replaced = node; this._text = node.textContent || ""; this.children = []; };
  el.style = {};
  el.setAttribute = function(k,v){ this._attrs = this._attrs||{}; this._attrs[k]=v; };
  el.getAttribute = function(k){ return (this._attrs||{})[k]; };
  el.querySelectorAll = function(){ return []; };
  return el;
}

function makeDoc(elements) {
  const doc = {
    readyState: "complete",
    documentElement: new FakeNode("html"),
    createElement(tag) { return makeEl(tag); },
    querySelectorAll(sel) {
      const tags = sel.split(",").map(s=>s.trim().toUpperCase());
      return elements.filter(e => tags.includes(e.tagName));
    },
    addEventListener(){},
  };
  return doc;
}

module.exports = { makeEl, makeDoc, FakeNode, FakeText };
