"use strict";

function alignedReader(root) {
  const original = root.querySelector("#source-pane");
  const translated = root.querySelector("#translation-pane");
  const lanes = [original, translated];
  const lists = lanes.map(lane => lane.querySelector(".passages"));
  const spacers = lanes.map(lane => lane.querySelector(".end-space"));
  const stacked = root.querySelector("#stacked");
  const compact = matchMedia("(max-width: 680px)");
  // Programmatic updates also emit scroll events. Ignore the coordinates set by the peer.
  const expectedScroll = new Map();
  let cards = [], anchor = {index:0, progress:0}, marked = null;

  function mark(index) {
    if (index === marked) return;
    for (const card of cards[marked] || []) card.classList.remove("current-passage");
    for (const card of cards[index]) card.classList.add("current-passage");
    marked = index;
  }

  function span(side, index) {
    const top = cards[index][side].offsetTop;
    const next = cards[index + 1]?.[side];
    const lane = lanes[side];
    const end = next ? next.offsetTop - 1 : lane.scrollHeight - lane.clientHeight;
    return Math.max(0, end - top);
  }

  function setPosition(side) {
    const lane = lanes[side];
    const {index, progress} = anchor;
    const top = cards[index][side].offsetTop;
    lane.scrollTop = top + Math.floor(span(side,index) * progress);
    expectedScroll.set(lane, lane.scrollTop);
  }

  function follow(side) {
    if (compact.matches || !cards.length) return;
    const lane = lanes[side];
    const position = lane.scrollTop;
    if (position === expectedScroll.get(lane)) return;
    expectedScroll.delete(lane);
    let first = 0, last = cards.length - 1;
    while (first < last) {
      const mid = Math.ceil((first + last) / 2);
      if (cards[mid][side].offsetTop <= position) first = mid;
      else last = mid - 1;
    }
    const distance = span(side,first);
    const progress = distance ? (position - cards[first][side].offsetTop) / distance : 0;
    anchor = {index:first, progress:Math.min(1, progress)};
    mark(first);
    setPosition(1-side);
  }

  function realign() {
    if (compact.matches || !cards.length) return;
    lanes.forEach((lane, side) => {
      const last = cards.at(-1)[side];
      // Let a short last passage reach the same reading line as any other passage.
      const padding = Math.max(0, lane.clientHeight - last.offsetHeight + 1) + "px";
      if (spacers[side].style.height !== padding) spacers[side].style.height = padding;
    });
    lanes.forEach((_, side) => setPosition(side));
  }

  function layout() {
    const narrow = compact.matches;
    for (const lane of lanes) lane.parentElement.hidden = narrow;
    stacked.hidden = !narrow;
    if (narrow) {
      const pairs = cards.map(pair => {
        const group = document.createElement("div");
        group.className = "stacked-pair";
        group.append(...pair);
        return group;
      });
      stacked.replaceChildren(...pairs);
      for (const list of lists) list.replaceChildren();
    } else {
      lists.forEach((list, side) => list.replaceChildren(...cards.map(pair => pair[side])));
      stacked.replaceChildren();
      realign();
    }
  }

  lanes.forEach((lane,side) => lane.addEventListener("scroll", () => follow(side)));
  const resize = new ResizeObserver(realign);
  for (const element of [...lanes, ...lists]) resize.observe(element);
  compact.addEventListener("change", layout);

  return {
    render(nextCards) {
      cards = nextCards;
      anchor = {index:0, progress:0};
      marked = null;
      expectedScroll.clear();
      root.hidden = !cards.length;
      layout();
      if (cards.length) mark(0);
    },
    locate(index, side) {
      const destination = cards[index][side];
      anchor = {index, progress:0};
      mark(index);
      if (compact.matches) destination.scrollIntoView({block:"start"});
      else realign();
      destination.focus({preventScroll:true});
    }
  };
}
