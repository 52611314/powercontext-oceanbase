interface Props {
  text: string
  ranges: Array<[number, number]>
}

export default function HighlightText({ text, ranges }: Props) {
  if (!ranges.length) {
    return <>{text}</>
  }
  const parts: Array<string | JSX.Element> = []
  let cursor = 0
  ranges.forEach(([start, end], index) => {
    if (start < cursor || end <= start) return
    if (start > cursor) parts.push(text.slice(cursor, start))
    parts.push(<mark key={index}>{text.slice(start, end)}</mark>)
    cursor = end
  })
  parts.push(text.slice(cursor))
  return <>{parts}</>
}
