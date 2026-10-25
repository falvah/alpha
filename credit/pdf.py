"""Dependency-free PDF attachments for seeded workspaces and tests."""
def make_pdf(label="ACCOUNT STATEMENT", lines=()):
    def escape(text):
        return str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").replace("\n", " ").replace("\r", " ")
    content = [f"BT /F1 20 Tf 50 760 Td ({escape(label)}) Tj /F1 12 Tf"]
    content.extend(f"0 -30 Td ({escape(line)}) Tj" for line in lines)
    content.append("0 -40 Td (Generated in sandbox. Not a bank-issued document.) Tj ET")
    stream = " ".join(content).encode("ascii")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    data = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets:
        data += f"{offset:010d} 00000 n \n".encode()
    return data + f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
