"""Time buckets retain latency ranges and failures without drawing thousands of points."""
from statistics import median


def bucket_samples(points, start, end, columns):
    columns = max(1, int(columns))
    span = max(1, end-start)
    buckets = {}
    previous = None
    interrupted = False
    for stamp, value, interval in points:
        if not start <= stamp <= end:
            continue
        index = min(columns-1, int((stamp-start)/span*columns))
        if index not in buckets:
            buckets[index] = dict(index=index, first=stamp, last=stamp, values=[], failed=False,
                                  break_before=False, interval=interval)
        b = buckets[index]
        b['last'] = stamp
        b['interval'] = max(interval, b['interval'])
        if value is None:
            b['failed'] = True
            interrupted = True
        else:
            if interrupted or (previous and stamp-previous[0] > max(30, interval*3, previous[1]*3)):
                b['break_before'] = True
            b['values'].append(value)
            interrupted = False
        previous = stamp, interval
    result = []
    for b in buckets.values():
        values = b.pop('values')
        b.update(low=min(values) if values else None, high=max(values) if values else None,
                 value=median(values) if values else None)
        result.append(b)
    return result


def paint_series(canvas, points, start, end, left, right, top, bottom, ceiling, color):
    buckets = bucket_samples(points, start, end, (right-left)/3)
    previous = None
    for b in buckets:
        x = left+((b['first']+b['last'])/2-start)/(end-start)*(right-left)
        if b['failed']:
            canvas.create_line(x, bottom-3, x, bottom, fill='#fb8c96', width=3)
        if b['value'] is None:
            previous = None
            continue
        y = bottom-b['value']/ceiling*(bottom-top)
        low = bottom-b['low']/ceiling*(bottom-top)
        high = bottom-b['high']/ceiling*(bottom-top)
        if b['high'] > b['low']:
            canvas.create_line(x, high, x, low, fill=color, width=1)
        if previous:
            if b['first']-previous[2] > max(30, b['interval']*3, previous[3]*3):
                canvas.create_rectangle(previous[0]+2, top, x-2, bottom-4, fill='#202c3d', outline='')
            elif not b['break_before'] and not b['failed']:
                canvas.create_line(previous[0], previous[1], x, y, fill=color, width=1.5,
                                   capstyle='round', joinstyle='round')
        canvas.create_oval(x-1, y-1, x+1, y+1, fill=color, outline='')
        previous = None if b['failed'] else (x, y, b['last'], b['interval'])
    return buckets
