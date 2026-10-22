import React from 'react';
import BooksDocumentsPage from './BooksDocumentsPage';

export function QuotesPage({ setError }) {
  return <BooksDocumentsPage type="quote" setError={setError} />;
}
