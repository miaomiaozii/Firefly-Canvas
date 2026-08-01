(() => {
  const started = Date.now();
  const record = (key, value) => {
    try {
      const all = JSON.parse(localStorage.getItem("perf500_obs") || "{}");
      all[key] = value;
      localStorage.setItem("perf500_obs", JSON.stringify(all));
    } catch (e) {}
  };
  record("page_loaded_ms", Date.now() - started);
  const poll = () => {
    const nodes = [...document.querySelectorAll(".image-node, .smart-node")];
    const imgs = [...document.querySelectorAll(".image-node .node-img")];
    const done = imgs.filter((i) => i.complete && i.naturalWidth > 0);
    const failed = imgs.filter((i) => i.complete && i.naturalWidth === 0);
    record("last_sample", {
      t: Date.now() - started,
      nodes: nodes.length,
      imgs: imgs.length,
      done: done.length,
      failed: failed.length,
      mem: Math.round((performance.memory ? performance.memory.usedJSHeapSize : 0) / 1048576),
    });
    if (imgs.length > 0 && done.length + failed.length === imgs.length) {
      record("all_loaded_ms", Date.now() - started);
      record("final", {
        nodes: nodes.length,
        imgs: imgs.length,
        done: done.length,
        failed: failed.length,
      });
    } else {
      setTimeout(poll, 3000);
    }
  };
  setTimeout(poll, 3000);
})();
