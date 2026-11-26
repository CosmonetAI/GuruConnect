import React from 'react';
import BooksDocumentsPage from './BooksDocumentsPage';

export function InvoicesPage({ setError }) {
  return <BooksDocumentsPage type="invoice" setError={setError} />;
}
