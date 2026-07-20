import "./globals.css";

export const metadata = {
  title: "Task Assistant",
  description: "Task management chatbot"
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
