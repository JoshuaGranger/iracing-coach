// Synchronized inspection for the race investigation. Blazor renders the
// charts and map at measured pixel size; this module only moves one cursor
// across every chart, the readouts and the map marker, so pointer motion never
// waits on a render round trip. It performs no telemetry calculation: rows and
// positions arrive from the backend projection through the rendered markup.
(function () {
  "use strict";
  const registry = new WeakMap();

  function readJson(element, name) {
    try { return JSON.parse(element.getAttribute(name) || "null"); } catch { return null; }
  }

  function format(value, digits, signed) {
    if (value === null || value === undefined || !Number.isFinite(value)) return "—";
    const text = Math.abs(value).toFixed(digits);
    if (!signed) return (value < 0 ? "−" : "") + text;
    return (value < 0 ? "−" : value > 0 ? "+" : "") + text;
  }

  function nearest(rows, x) {
    let best = 0;
    for (let index = 1; index < rows.length; index++) {
      if (Math.abs(rows[index].x - x) < Math.abs(rows[best].x - x)) best = index;
    }
    return best;
  }

  function attach(root) {
    if (!root || registry.has(root)) return;
    const rows = readJson(root, "data-rows") || [];
    if (!rows.length) return;
    const start = Number(root.getAttribute("data-start"));
    const end = Number(root.getAttribute("data-end"));
    const state = { index: Number(root.getAttribute("data-initial") || 0), frame: 0, pending: null };
    const charts = Array.from(root.querySelectorAll("[data-ri-chart]"));
    const map = root.querySelector("[data-ri-map]");
    const mapPoints = map ? readJson(map, "data-points") || [] : [];
    const marker = map ? map.querySelector("[data-ri-map-cursor]") : null;
    const position = root.querySelector("[data-ri-position]");

    function render() {
      const row = rows[state.index];
      if (!row) return;
      const fraction = (row.x - start) / Math.max(1e-9, end - start);
      for (const chart of charts) {
        const left = Number(chart.getAttribute("data-left"));
        const width = Number(chart.getAttribute("data-width"));
        const cursor = chart.querySelector("[data-ri-cursor]");
        if (cursor) {
          const x = left + fraction * width;
          cursor.setAttribute("x1", x);
          cursor.setAttribute("x2", x);
        }
      }
      for (const output of root.querySelectorAll("[data-ri-read]")) {
        const key = output.getAttribute("data-ri-read");
        const digits = Number(output.getAttribute("data-digits") || 1);
        if (key === "gap") {
          output.textContent = format(row.gap, 3, true);
        } else {
          const side = output.getAttribute("data-side");
          const value = row[side] ? row[side][key] : null;
          output.textContent = format(value, digits, false);
        }
      }
      if (position) position.textContent = (row.x * 100).toFixed(1) + "% lap";
      if (marker && mapPoints.length) {
        let best = mapPoints[0];
        for (const point of mapPoints) if (Math.abs(point[2] - row.x) < Math.abs(best[2] - row.x)) best = point;
        marker.setAttribute("cx", best[0]);
        marker.setAttribute("cy", best[1]);
        marker.style.display = "";
      }
    }

    function schedule(x) {
      state.pending = x;
      if (state.frame) return;
      state.frame = requestAnimationFrame(() => {
        state.frame = 0;
        state.index = nearest(rows, state.pending);
        render();
      });
    }

    function fromChart(chart, event) {
      const box = chart.getBoundingClientRect();
      const scale = box.width / Math.max(1, Number(chart.getAttribute("data-view-width")) || box.width);
      const left = Number(chart.getAttribute("data-left")) * scale;
      const width = Number(chart.getAttribute("data-width")) * scale;
      const fraction = Math.max(0, Math.min(1, (event.clientX - box.left - left) / Math.max(1, width)));
      schedule(start + fraction * (end - start));
    }

    function onKey(event) {
      let next = state.index;
      if (event.key === "ArrowRight" || event.key === "ArrowUp") next++;
      else if (event.key === "ArrowLeft" || event.key === "ArrowDown") next--;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = rows.length - 1;
      else return;
      event.preventDefault();
      state.index = Math.max(0, Math.min(rows.length - 1, next));
      render();
    }

    const listeners = [];
    function listen(element, name, handler) {
      element.addEventListener(name, handler);
      listeners.push(() => element.removeEventListener(name, handler));
    }

    for (const chart of charts) {
      listen(chart, "pointermove", event => fromChart(chart, event));
      listen(chart, "pointerdown", event => {
        chart.focus({ preventScroll: true });
        if (event.pointerType !== "mouse") chart.setPointerCapture(event.pointerId);
        fromChart(chart, event);
      });
      listen(chart, "keydown", onKey);
    }
    if (map && mapPoints.length) {
      listen(map, "pointermove", event => {
        const box = map.getBoundingClientRect();
        const view = map.viewBox && map.viewBox.baseVal;
        if (!view || !view.width) return;
        const scale = Math.min(box.width / view.width, box.height / view.height);
        const offsetX = (box.width - view.width * scale) / 2;
        const offsetY = (box.height - view.height * scale) / 2;
        const x = (event.clientX - box.left - offsetX) / scale + view.x;
        const y = (event.clientY - box.top - offsetY) / scale + view.y;
        let best = null;
        let distance = Infinity;
        for (const point of mapPoints) {
          const d = (point[0] - x) ** 2 + (point[1] - y) ** 2;
          if (d < distance) { distance = d; best = point; }
        }
        // Only the highlighted approach and section inspect; the rest of the lap has no pair data.
        if (best && best[2] >= start && best[2] <= end) schedule(best[2]);
      });
      listen(map, "keydown", onKey);
    }
    registry.set(root, () => {
      if (state.frame) cancelAnimationFrame(state.frame);
      for (const remove of listeners) remove();
    });
    render();
  }

  function detach(root) {
    const dispose = root && registry.get(root);
    if (dispose) { dispose(); registry.delete(root); }
  }

  const observers = new WeakMap();
  window.raceInspection = {
    // Called after every render that may have replaced the markup.
    refresh: function (root) { detach(root); attach(root); },
    detach: detach,
    size: function (element) {
      if (!element || !element.getBoundingClientRect) return [0, 0];
      const box = element.getBoundingClientRect();
      return [box.width || 0, box.height || 0];
    },
    observeSize: function (element, reference) {
      if (!element || observers.has(element)) return;
      let last = "";
      const observer = new ResizeObserver(entries => {
        const box = entries[0].contentRect;
        const size = Math.round(box.width) + "x" + Math.round(box.height);
        if (size === last || box.width < 2 || box.height < 2) return;
        last = size;
        reference.invokeMethodAsync("OnMeasured", box.width, box.height).catch(() => {});
      });
      observer.observe(element);
      observers.set(element, observer);
    },
    unobserve: function (element) {
      const observer = element && observers.get(element);
      if (observer) { observer.disconnect(); observers.delete(element); }
    }
  };
})();
