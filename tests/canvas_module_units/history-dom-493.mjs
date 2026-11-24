// Offline tree adapter: parses rendered markup and routes production event bindings; not a layout engine.
const decode = s => s.replace(/&(amp|lt|gt|quot|#39);/g, (_, k) => ({amp:'&',lt:'<',gt:'>',quot:'"','#39':"'"})[k]);
export class Element {
    constructor(tag='div', attrs={}) { this.tagName=tag.toUpperCase();this.attrs=attrs;this.children=[];this.style={};this.scrollLeft=0;this.clientWidth=1300;this.scrollWidth=1300;this.classList={add(){},remove(){},toggle(){}}; }
    get dataset(){return Object.fromEntries(Object.entries(this.attrs).filter(([k])=>k.startsWith('data-')).map(([k,v])=>[k.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase()),v]));}
    get disabled(){return 'disabled' in this.attrs;}
    get hidden(){return 'hidden' in this.attrs;}
    set hidden(v){v?this.attrs.hidden='':delete this.attrs.hidden;}
    setAttribute(k,v){this.attrs[k]=String(v);}
    getAttribute(k){return this.attrs[k]??null;}
    hasAttribute(k){return k in this.attrs;}
    removeAttribute(k){delete this.attrs[k];}
    appendChild(el){el.parentNode=this;this.children.push(el);return el;}
    contains(el){return this===el||this.children.some(c=>c.contains(el));}
    matches(selector){return selector.split(',').some(s=>{s=s.trim();const tag=s.match(/^[\w-]+/);if(tag&&tag[0].toUpperCase()!==this.tagName)return false;for(const m of s.matchAll(/\.([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]/g)){if(m[1]&&!(this.attrs.class||'').split(/\s+/).includes(m[1]))return false;if(m[2]&&(!(m[2] in this.attrs)||(m[3]!==undefined&&this.attrs[m[2]]!==m[3])))return false;}return true;});}
    closest(s){return this.matches(s)?this:this.parentNode?.closest(s)||null;}
    querySelectorAll(s){return this.children.flatMap(c=>[...(c.matches(s)?[c]:[]),...c.querySelectorAll(s)]);}
    querySelector(s){return this.querySelectorAll(s)[0]||null;}
    getBoundingClientRect(){return {width:238,height:274};}
    addEventListener(){}
    focus(){}
    set innerHTML(html){this.markup=html;this.children=[];const stack=[this];for(const token of html.match(/<[^>]+>|[^<]+/g)||[]){if(token.startsWith('</')){const last=stack.pop();if(last.tagName!==token.slice(2,-1).toUpperCase())throw Error('Unbalanced history markup');}else if(token.startsWith('<')){const tag=token.match(/^<([\w-]+)/)[1];const attrs=Object.fromEntries([...token.matchAll(/([\w-]+)(?:="([^"]*)")?/g)].slice(1).map(m=>[m[1],decode(m[2]||'')]));const child=stack.at(-1).appendChild(new Element(tag,attrs));if(!token.endsWith('/>')&&!['img','input','br','use'].includes(tag))stack.push(child);}else stack.at(-1).textContent=(stack.at(-1).textContent||'')+decode(token);}if(stack.length!==1)throw Error('Unclosed history markup');}
    get innerHTML(){return this.markup||'';}
}
