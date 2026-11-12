const labels = {"topic": "Chủ đề", "discussion": "Thảo luận", "decision": "Quyết định", "action": "Công việc", "open_question": "Vấn đề còn mở", "proposal": "Đề xuất", "risk": "Rủi ro", "support": "Lập luận ủng hộ", "opposition": "Lập luận phản đối", "rebuttal": "Phản biện", "disagreement": "Điểm bất đồng", "message": "Thông điệp chính", "example": "Số liệu và ví dụ", "call_to_action": "Lời kêu gọi"};
export function renderSummary(summary, renderSources) {
  const box = document.createElement('div');
  const addHeading = (parent, tag, text) => {
    const heading = document.createElement(tag);
    heading.textContent = text;
    parent.append(heading);
  };
  const addPoint = (parent, point) => {
    const paragraph = document.createElement('p');
    paragraph.textContent = point.text;
    if (point.category === 'action') {
      if (point.owner) paragraph.textContent += ` · Phụ trách: ${point.owner}`;
      if (point.deadline) paragraph.textContent += ` · Hạn: ${point.deadline}`;
    }
    parent.append(paragraph, renderSources(point.ids));
  };
  if (summary.format === 'minutes') {
    // Fixed minutes layout; an empty section is not shown at all.
    const minutes = [['decision', 'Quyết định'], ['action', 'Việc cần làm'],
      ['discussion', summary.kind === 'talk' ? 'Nội dung chính' : 'Vấn đề đã thảo luận'], ['open_question', 'Còn bỏ ngỏ']];
    if (summary.overview?.length) {
      addHeading(box, 'h3', 'Tổng quan');
      for (const point of summary.overview) addPoint(box, point);
    }
    const points = (summary.sections || []).flatMap(section => section.bullets || []);
    for (const [category, label] of minutes) {
      const rows = points.filter(point => point.category === category);
      if (!rows.length) continue;
      addHeading(box, 'h3', label);
      for (const point of rows) addPoint(box, point);
    }
    return box;
  }
  if (summary.overview?.length) {
    addHeading(box, 'h3', 'Tóm tắt nội dung');
    for (const point of summary.overview) addPoint(box, point);
  }
  const groups = new Map();
  for (const section of summary.sections || []) {
    for (const point of section.bullets || []) {
      const label = labels[point.category] || labels.discussion;
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label).push(point);
    }
  }
  if (groups.size) addHeading(box, 'h3', 'Phân tích theo transcript');
  for (const [label, points] of groups) {
    addHeading(box, 'h4', label);
    for (const point of points) addPoint(box, point);
  }
  return box;
}
