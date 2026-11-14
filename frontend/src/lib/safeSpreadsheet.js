import ExcelJS from "exceljs";

const utils = {
    book_new: () => new ExcelJS.Workbook(),
    aoa_to_sheet: (rows) => ({ rows, "!cols": [] }),
    book_append_sheet: (workbook, sheetData, name) => {
        const worksheet = workbook.addWorksheet(name);
        worksheet.addRows(sheetData.rows || []);
        (sheetData["!cols"] || []).forEach((column, index) => {
            worksheet.getColumn(index + 1).width = column.wch;
        });
        return worksheet;
    },
};

const writeFile = async (workbook, filename, { shouldContinue } = {}) => {
    if (shouldContinue && !shouldContinue()) return false;
    const buffer = await workbook.xlsx.writeBuffer();
    // Serialization is asynchronous. A scoped caller can revoke the download
    // before any object URL or anchor is created, even after data was accepted.
    if (shouldContinue && !shouldContinue()) return false;
    const blob = new Blob(
        [buffer],
        {
            type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        },
    );
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
};

const SafeSpreadsheet = { utils, writeFile };

export default SafeSpreadsheet;
