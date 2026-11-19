// Formatting an amount or rendering a report must not download the Excel writer.
test('the specialist report and its order dialogs keep spreadsheet initialization off their read path', () => {
  const spreadsheet = jest.fn(() => { throw new Error('Spreadsheet initialized outside export'); });
  jest.isolateModules(() => {
    jest.doMock('../../lib/safeSpreadsheet', spreadsheet);
    expect(typeof require('./OperationalFinancialPosition').default).toBe('function');
    expect(typeof require('./PendingOrdersDialog').default).toBe('function');
    expect(require('./financialPositionDisplay').formatPositionMoney('1234.50')).toBe('1,234.50');
  });
  expect(spreadsheet).not.toHaveBeenCalled();
});
