//! Ordering as CPython 3.12 performs it with `<` alone: `list.sort`/`sorted` (timsort with
//! the powersort merge policy, `Objects/listobject.c`), `min`/`max`, `bisect` and `heapq`.
//!
//! For a consistent order any stable sort gives the same result; the port matters when
//! the order is not consistent, which is the case as soon as a NaN is present: CPython's
//! comparison sequence then decides where every item lands, and this module repeats it.

use crate::num::{Num, py_gt, py_lt};

const MIN_GALLOP: usize = 7;

#[derive(Clone, Copy)]
struct Run {
    base: usize,
    len: usize,
    power: u32,
}

struct MergeState<'f> {
    lt: &'f mut dyn FnMut(usize, usize) -> bool,
    min_gallop: usize,
    pending: Vec<Run>,
    tmp: Vec<usize>,
    listlen: usize,
}

enum Exit {
    Done,
    Copy,
}

impl MergeState<'_> {
    fn lt(&mut self, a: usize, b: usize) -> bool {
        (self.lt)(a, b)
    }

    fn count_run(&mut self, a: &[usize], lo: usize, hi: usize) -> (usize, bool) {
        if lo + 1 == hi {
            return (1, false);
        }
        let mut n = 2;
        if self.lt(a[lo + 1], a[lo]) {
            for i in lo + 2..hi {
                if self.lt(a[i], a[i - 1]) {
                    n += 1;
                } else {
                    break;
                }
            }
            (n, true)
        } else {
            for i in lo + 2..hi {
                if self.lt(a[i], a[i - 1]) {
                    break;
                }
                n += 1;
            }
            (n, false)
        }
    }

    fn binarysort(&mut self, a: &mut [usize], lo: usize, hi: usize, mut start: usize) {
        if start == lo {
            start += 1;
        }
        for s in start..hi {
            let pivot = a[s];
            let (mut l, mut r) = (lo, s);
            loop {
                let p = l + ((r - l) >> 1);
                if self.lt(pivot, a[p]) {
                    r = p;
                } else {
                    l = p + 1;
                }
                if l >= r {
                    break;
                }
            }
            a.copy_within(l..s, l + 1);
            a[l] = pivot;
        }
    }

    /// Leftmost position for `key` in `slice[..n]` (`a[k-1] < key <= a[k]`).
    fn gallop_left(&mut self, key: usize, slice: &[usize], n: usize, hint: usize) -> usize {
        let mut lastofs: isize = 0;
        let mut ofs: isize = 1;
        let hint_i = hint as isize;
        if self.lt(slice[hint], key) {
            let maxofs = (n - hint) as isize;
            while ofs < maxofs {
                if self.lt(slice[hint + ofs as usize], key) {
                    lastofs = ofs;
                    ofs = (ofs << 1) + 1;
                } else {
                    break;
                }
            }
            if ofs > maxofs {
                ofs = maxofs;
            }
            lastofs += hint_i;
            ofs += hint_i;
        } else {
            let maxofs = hint_i + 1;
            while ofs < maxofs {
                if self.lt(slice[hint - ofs as usize], key) {
                    break;
                }
                lastofs = ofs;
                ofs = (ofs << 1) + 1;
            }
            if ofs > maxofs {
                ofs = maxofs;
            }
            let k = lastofs;
            lastofs = hint_i - ofs;
            ofs = hint_i - k;
        }
        lastofs += 1;
        while lastofs < ofs {
            let m = lastofs + ((ofs - lastofs) >> 1);
            if self.lt(slice[m as usize], key) {
                lastofs = m + 1;
            } else {
                ofs = m;
            }
        }
        ofs as usize
    }

    /// Rightmost position for `key` in `slice[..n]` (`a[k-1] <= key < a[k]`).
    fn gallop_right(&mut self, key: usize, slice: &[usize], n: usize, hint: usize) -> usize {
        let mut lastofs: isize = 0;
        let mut ofs: isize = 1;
        let hint_i = hint as isize;
        if self.lt(key, slice[hint]) {
            let maxofs = hint_i + 1;
            while ofs < maxofs {
                if self.lt(key, slice[hint - ofs as usize]) {
                    lastofs = ofs;
                    ofs = (ofs << 1) + 1;
                } else {
                    break;
                }
            }
            if ofs > maxofs {
                ofs = maxofs;
            }
            let k = lastofs;
            lastofs = hint_i - ofs;
            ofs = hint_i - k;
        } else {
            let maxofs = (n - hint) as isize;
            while ofs < maxofs {
                if self.lt(key, slice[hint + ofs as usize]) {
                    break;
                }
                lastofs = ofs;
                ofs = (ofs << 1) + 1;
            }
            if ofs > maxofs {
                ofs = maxofs;
            }
            lastofs += hint_i;
            ofs += hint_i;
        }
        lastofs += 1;
        while lastofs < ofs {
            let m = lastofs + ((ofs - lastofs) >> 1);
            if self.lt(key, slice[m as usize]) {
                ofs = m;
            } else {
                lastofs = m + 1;
            }
        }
        ofs as usize
    }

    fn merge_lo(
        &mut self,
        a: &mut [usize],
        pa: usize,
        mut na: usize,
        mut pb: usize,
        mut nb: usize,
    ) {
        let mut tmp = std::mem::take(&mut self.tmp);
        tmp.clear();
        tmp.extend_from_slice(&a[pa..pa + na]);
        let mut ta = 0usize;
        let mut dest = pa;
        a[dest] = a[pb];
        dest += 1;
        pb += 1;
        nb -= 1;
        let exit = 'merge: {
            if nb == 0 {
                break 'merge Exit::Done;
            }
            if na == 1 {
                break 'merge Exit::Copy;
            }
            let mut min_gallop = self.min_gallop;
            loop {
                let mut acount = 0usize;
                let mut bcount = 0usize;
                loop {
                    if self.lt(a[pb], tmp[ta]) {
                        a[dest] = a[pb];
                        dest += 1;
                        pb += 1;
                        bcount += 1;
                        acount = 0;
                        nb -= 1;
                        if nb == 0 {
                            break 'merge Exit::Done;
                        }
                        if bcount >= min_gallop {
                            break;
                        }
                    } else {
                        a[dest] = tmp[ta];
                        dest += 1;
                        ta += 1;
                        acount += 1;
                        bcount = 0;
                        na -= 1;
                        if na == 1 {
                            break 'merge Exit::Copy;
                        }
                        if acount >= min_gallop {
                            break;
                        }
                    }
                }
                min_gallop += 1;
                loop {
                    min_gallop -= usize::from(min_gallop > 1);
                    self.min_gallop = min_gallop;
                    let k = self.gallop_right(a[pb], &tmp[ta..ta + na], na, 0);
                    acount = k;
                    if k > 0 {
                        a[dest..dest + k].copy_from_slice(&tmp[ta..ta + k]);
                        dest += k;
                        ta += k;
                        na -= k;
                        if na == 1 {
                            break 'merge Exit::Copy;
                        }
                        if na == 0 {
                            break 'merge Exit::Done;
                        }
                    }
                    a[dest] = a[pb];
                    dest += 1;
                    pb += 1;
                    nb -= 1;
                    if nb == 0 {
                        break 'merge Exit::Done;
                    }
                    let k = self.gallop_left(tmp[ta], &a[pb..pb + nb], nb, 0);
                    bcount = k;
                    if k > 0 {
                        a.copy_within(pb..pb + k, dest);
                        dest += k;
                        pb += k;
                        nb -= k;
                        if nb == 0 {
                            break 'merge Exit::Done;
                        }
                    }
                    a[dest] = tmp[ta];
                    dest += 1;
                    ta += 1;
                    na -= 1;
                    if na == 1 {
                        break 'merge Exit::Copy;
                    }
                    if !(acount >= MIN_GALLOP || bcount >= MIN_GALLOP) {
                        break;
                    }
                }
                min_gallop += 1;
                self.min_gallop = min_gallop;
            }
        };
        match exit {
            Exit::Done => {
                if na > 0 {
                    a[dest..dest + na].copy_from_slice(&tmp[ta..ta + na]);
                }
            }
            Exit::Copy => {
                a.copy_within(pb..pb + nb, dest);
                a[dest + nb] = tmp[ta];
            }
        }
        self.tmp = tmp;
    }

    fn merge_hi(&mut self, a: &mut [usize], pa: usize, mut na: usize, pb: usize, mut nb: usize) {
        let mut tmp = std::mem::take(&mut self.tmp);
        tmp.clear();
        tmp.extend_from_slice(&a[pb..pb + nb]);
        let mut dest = (pb + nb - 1) as isize;
        let mut ia = (pa + na - 1) as isize;
        let mut ib = nb as isize - 1;
        a[dest as usize] = a[ia as usize];
        dest -= 1;
        ia -= 1;
        na -= 1;
        let exit = 'merge: {
            if na == 0 {
                break 'merge Exit::Done;
            }
            if nb == 1 {
                break 'merge Exit::Copy;
            }
            let mut min_gallop = self.min_gallop;
            loop {
                let mut acount = 0usize;
                let mut bcount = 0usize;
                loop {
                    if self.lt(tmp[ib as usize], a[ia as usize]) {
                        a[dest as usize] = a[ia as usize];
                        dest -= 1;
                        ia -= 1;
                        acount += 1;
                        bcount = 0;
                        na -= 1;
                        if na == 0 {
                            break 'merge Exit::Done;
                        }
                        if acount >= min_gallop {
                            break;
                        }
                    } else {
                        a[dest as usize] = tmp[ib as usize];
                        dest -= 1;
                        ib -= 1;
                        bcount += 1;
                        acount = 0;
                        nb -= 1;
                        if nb == 1 {
                            break 'merge Exit::Copy;
                        }
                        if bcount >= min_gallop {
                            break;
                        }
                    }
                }
                min_gallop += 1;
                loop {
                    min_gallop -= usize::from(min_gallop > 1);
                    self.min_gallop = min_gallop;
                    let k = na - self.gallop_right(tmp[ib as usize], &a[pa..pa + na], na, na - 1);
                    acount = k;
                    if k > 0 {
                        dest -= k as isize;
                        ia -= k as isize;
                        let from = (ia + 1) as usize;
                        a.copy_within(from..from + k, (dest + 1) as usize);
                        na -= k;
                        if na == 0 {
                            break 'merge Exit::Done;
                        }
                    }
                    a[dest as usize] = tmp[ib as usize];
                    dest -= 1;
                    ib -= 1;
                    nb -= 1;
                    if nb == 1 {
                        break 'merge Exit::Copy;
                    }
                    let k = nb - self.gallop_left(a[ia as usize], &tmp[..nb], nb, nb - 1);
                    bcount = k;
                    if k > 0 {
                        dest -= k as isize;
                        ib -= k as isize;
                        let from = (ib + 1) as usize;
                        let to = (dest + 1) as usize;
                        a[to..to + k].copy_from_slice(&tmp[from..from + k]);
                        nb -= k;
                        if nb == 1 {
                            break 'merge Exit::Copy;
                        }
                        if nb == 0 {
                            break 'merge Exit::Done;
                        }
                    }
                    a[dest as usize] = a[ia as usize];
                    dest -= 1;
                    ia -= 1;
                    na -= 1;
                    if na == 0 {
                        break 'merge Exit::Done;
                    }
                    if !(acount >= MIN_GALLOP || bcount >= MIN_GALLOP) {
                        break;
                    }
                }
                min_gallop += 1;
                self.min_gallop = min_gallop;
            }
        };
        match exit {
            Exit::Done => {
                if nb > 0 {
                    let to = (dest - (nb as isize - 1)) as usize;
                    a[to..to + nb].copy_from_slice(&tmp[..nb]);
                }
            }
            Exit::Copy => {
                let to = (dest + 1 - na as isize) as usize;
                let from = (ia + 1 - na as isize) as usize;
                a.copy_within(from..from + na, to);
                dest -= na as isize;
                a[dest as usize] = tmp[ib as usize];
            }
        }
        self.tmp = tmp;
    }

    fn merge_at(&mut self, a: &mut [usize], i: usize) {
        let n = self.pending.len();
        let Run {
            base: mut pa,
            len: mut na,
            ..
        } = self.pending[i];
        let Run {
            base: pb, len: nb, ..
        } = self.pending[i + 1];
        self.pending[i].len = na + nb;
        if i + 3 == n {
            self.pending[i + 1] = self.pending[i + 2];
        }
        self.pending.pop();
        let k = self.gallop_right(a[pb], &a[pa..pa + na], na, 0);
        pa += k;
        na -= k;
        if na == 0 {
            return;
        }
        let nb = self.gallop_left(a[pa + na - 1], &a[pb..pb + nb], nb, nb - 1);
        if nb == 0 {
            return;
        }
        if na <= nb {
            self.merge_lo(a, pa, na, pb, nb);
        } else {
            self.merge_hi(a, pa, na, pb, nb);
        }
    }

    fn found_new_run(&mut self, a: &mut [usize], n2: usize) {
        if let Some(top) = self.pending.last().copied() {
            let power = powerloop(top.base, top.len, n2, self.listlen);
            while self.pending.len() > 1 && self.pending[self.pending.len() - 2].power > power {
                let i = self.pending.len() - 2;
                self.merge_at(a, i);
            }
            let last = self.pending.len() - 1;
            self.pending[last].power = power;
        }
    }

    fn merge_force_collapse(&mut self, a: &mut [usize]) {
        while self.pending.len() > 1 {
            let mut n = self.pending.len() - 2;
            if n > 0 && self.pending[n - 1].len < self.pending[n + 1].len {
                n -= 1;
            }
            self.merge_at(a, n);
        }
    }
}

fn powerloop(s1: usize, n1: usize, n2: usize, n: usize) -> u32 {
    let mut result = 0;
    let mut a = 2 * s1 + n1;
    let mut b = a + n1 + n2;
    loop {
        result += 1;
        if a >= n {
            a -= n;
            b -= n;
        } else if b >= n {
            break;
        }
        a <<= 1;
        b <<= 1;
    }
    result
}

fn merge_compute_minrun(mut n: usize) -> usize {
    let mut r = 0;
    while n >= 64 {
        r |= n & 1;
        n >>= 1;
    }
    n + r
}

/// The permutation `sorted(range(n), key=..., reverse=reverse)` produces when items are
/// compared with `lt(i, j)` (`key(i) < key(j)`): CPython's `list.sort`, step for step.
pub fn sort_permutation(
    n: usize,
    lt: &mut dyn FnMut(usize, usize) -> bool,
    reverse: bool,
) -> Vec<usize> {
    let mut a: Vec<usize> = (0..n).collect();
    if n < 2 {
        return a;
    }
    if reverse {
        a.reverse();
    }
    let mut ms = MergeState {
        lt,
        min_gallop: MIN_GALLOP,
        pending: Vec::new(),
        tmp: Vec::new(),
        listlen: n,
    };
    let minrun = merge_compute_minrun(n);
    let mut lo = 0;
    let mut remaining = n;
    while remaining > 0 {
        let (mut run, descending) = ms.count_run(&a, lo, lo + remaining);
        if descending {
            a[lo..lo + run].reverse();
        }
        if run < minrun {
            let force = remaining.min(minrun);
            ms.binarysort(&mut a, lo, lo + force, lo + run);
            run = force;
        }
        ms.found_new_run(&mut a, run);
        ms.pending.push(Run {
            base: lo,
            len: run,
            power: 0,
        });
        lo += run;
        remaining -= run;
    }
    ms.merge_force_collapse(&mut a);
    if reverse {
        a.reverse();
    }
    a
}

/// `items.sort(key=..., reverse=...)` with `lt` comparing two items (or their keys).
pub fn sort_by<T: Clone>(items: &mut [T], mut lt: impl FnMut(&T, &T) -> bool, reverse: bool) {
    let order = {
        let view: &[T] = items;
        sort_permutation(view.len(), &mut |i, j| lt(&view[i], &view[j]), reverse)
    };
    let sorted: Vec<T> = order.iter().map(|&i| items[i].clone()).collect();
    items.clone_from_slice(&sorted);
}

/// `sorted(values, reverse=reverse)` for Python numbers.
pub fn sorted_nums(values: &[Num], reverse: bool) -> Vec<Num> {
    let order = sort_permutation(
        values.len(),
        &mut |i, j| py_lt(values[i], values[j]),
        reverse,
    );
    order.iter().map(|&i| values[i]).collect()
}

/// `sorted(values, reverse=reverse)` for floats.
pub fn sorted_floats(values: &[f64], reverse: bool) -> Vec<f64> {
    let order = sort_permutation(values.len(), &mut |i, j| values[i] < values[j], reverse);
    order.iter().map(|&i| values[i]).collect()
}

/// Index `min(range(len(items)), key=...)` picks: the first item that no later item is `<`
/// than in sequence (`None` when empty).
pub fn min_index_by(len: usize, mut lt: impl FnMut(usize, usize) -> bool) -> Option<usize> {
    let mut best = if len == 0 { return None } else { 0 };
    for i in 1..len {
        if lt(i, best) {
            best = i;
        }
    }
    Some(best)
}

/// Index `max(...)` picks: replaced only when a later item is `>` the current one.
pub fn max_index_by(len: usize, mut gt: impl FnMut(usize, usize) -> bool) -> Option<usize> {
    let mut best = if len == 0 { return None } else { 0 };
    for i in 1..len {
        if gt(i, best) {
            best = i;
        }
    }
    Some(best)
}

/// `min(values)` for Python numbers.
pub fn min_num(values: &[Num]) -> Option<Num> {
    min_index_by(values.len(), |i, j| py_lt(values[i], values[j])).map(|i| values[i])
}

/// `max(values)` for Python numbers.
pub fn max_num(values: &[Num]) -> Option<Num> {
    max_index_by(values.len(), |i, j| py_gt(values[i], values[j])).map(|i| values[i])
}

/// `bisect.bisect_left(a, x)` with `lt(item, x)` meaning `a[i] < x`.
pub fn bisect_left_by(len: usize, mut item_lt_x: impl FnMut(usize) -> bool) -> usize {
    let (mut lo, mut hi) = (0, len);
    while lo < hi {
        let mid = (lo + hi) / 2;
        if item_lt_x(mid) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    lo
}

/// `bisect.bisect_right(a, x)` with `x_lt(item)` meaning `x < a[i]`.
pub fn bisect_right_by(len: usize, mut x_lt_item: impl FnMut(usize) -> bool) -> usize {
    let (mut lo, mut hi) = (0, len);
    while lo < hi {
        let mid = (lo + hi) / 2;
        if x_lt_item(mid) {
            hi = mid;
        } else {
            lo = mid + 1;
        }
    }
    lo
}

/// `bisect.bisect_left(values, x)` for Python numbers.
pub fn bisect_left(values: &[Num], x: Num) -> usize {
    bisect_left_by(values.len(), |i| py_lt(values[i], x))
}

/// `bisect.bisect_right(values, x)` for Python numbers.
pub fn bisect_right(values: &[Num], x: Num) -> usize {
    bisect_right_by(values.len(), |i| py_lt(x, values[i]))
}

fn sift_down<T>(heap: &mut [T], start: usize, mut pos: usize, lt: &mut impl FnMut(&T, &T) -> bool) {
    while pos > start {
        let parent = (pos - 1) >> 1;
        if !lt(&heap[pos], &heap[parent]) {
            break;
        }
        heap.swap(pos, parent);
        pos = parent;
    }
}

fn sift_up<T>(heap: &mut [T], mut pos: usize, lt: &mut impl FnMut(&T, &T) -> bool) {
    let end = heap.len();
    let start = pos;
    let limit = end >> 1;
    while pos < limit {
        let mut child = 2 * pos + 1;
        if child + 1 < end && !lt(&heap[child], &heap[child + 1]) {
            child += 1;
        }
        heap.swap(child, pos);
        pos = child;
    }
    sift_down(heap, start, pos, lt);
}

/// `heapq.heappush(heap, item)`.
pub fn heap_push<T>(heap: &mut Vec<T>, item: T, mut lt: impl FnMut(&T, &T) -> bool) {
    heap.push(item);
    let last = heap.len() - 1;
    sift_down(heap, 0, last, &mut lt);
}

/// `heapq.heappop(heap)`.
pub fn heap_pop<T>(heap: &mut Vec<T>, mut lt: impl FnMut(&T, &T) -> bool) -> Option<T> {
    let last = heap.pop()?;
    if heap.is_empty() {
        return Some(last);
    }
    let top = std::mem::replace(&mut heap[0], last);
    sift_up(heap, 0, &mut lt);
    Some(top)
}

/// `heapq.heapreplace(heap, item)`: pop the smallest, then push `item`, in one sift.
pub fn heap_replace<T>(heap: &mut [T], item: T, mut lt: impl FnMut(&T, &T) -> bool) -> Option<T> {
    if heap.is_empty() {
        return None;
    }
    let top = std::mem::replace(&mut heap[0], item);
    sift_up(heap, 0, &mut lt);
    Some(top)
}
