using System;
using System.IO;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    public sealed class CommandReceiptJournalTests
    {
        private string _root;

        [SetUp]
        public void SetUp()
        {
            _root = Path.Combine(
                Path.GetTempPath(),
                "unity-mcp-command-receipts-" + Guid.NewGuid().ToString("N"));
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(_root)) Directory.Delete(_root, true);
        }

        [Test]
        public void CompletedReceiptSurvivesNewJournalAndReturnsOriginalResult()
        {
            var journal = new CommandReceiptJournal(_root);
            CommandReceipt receipt = journal.Begin(
                "command-one", "project", "probe", new string('a', 64), out bool created);
            Assert.That(created, Is.True);
            journal.Complete(receipt, new JObject { ["count"] = 1 });

            var reloaded = new CommandReceiptJournal(_root);
            CommandReceipt duplicate = reloaded.Begin(
                "command-one", "project", "probe", new string('a', 64), out created);

            Assert.That(created, Is.False);
            Assert.That(duplicate.State, Is.EqualTo("completed"));
            Assert.That(duplicate.Result.Value<int>("count"), Is.EqualTo(1));
        }

        [Test]
        public void StartedDuplicateStaysStartedAndIsNotRecreated()
        {
            var journal = new CommandReceiptJournal(_root);
            journal.Begin(
                "command-two", "project", "probe", new string('b', 64), out bool created);
            Assert.That(created, Is.True);

            CommandReceipt duplicate = journal.Begin(
                "command-two", "project", "probe", new string('b', 64), out created);

            Assert.That(created, Is.False);
            Assert.That(duplicate.State, Is.EqualTo("started"));
        }

        [Test]
        public void DuplicateIdWithDifferentEnvelopeIsRejected()
        {
            var journal = new CommandReceiptJournal(_root);
            journal.Begin(
                "command-three", "project", "probe", new string('c', 64), out _);

            Assert.Throws<InvalidDataException>(() => journal.Begin(
                "command-three", "project", "probe", new string('d', 64), out _));
        }

        [Test]
        public void AcknowledgeDeletesOnlyExactReceipt()
        {
            var journal = new CommandReceiptJournal(_root);
            journal.Begin("one", "project", "probe", new string('e', 64), out _);
            journal.Begin("two", "project", "probe", new string('f', 64), out _);

            journal.Acknowledge("one");

            Assert.That(journal.All(), Has.Count.EqualTo(1));
            Assert.That(journal.All()[0].Id, Is.EqualTo("two"));
        }

        [Test]
        public void ExpiredReceiptIsRemovedBeforeCapacityOrReplayChecks()
        {
            var journal = new CommandReceiptJournal(_root);
            journal.Begin("expired", "project", "probe", new string('a', 64), out _);
            string path = Path.Combine(_root, "expired.json");
            File.SetLastWriteTimeUtc(
                path,
                DateTime.UtcNow - CommandReceiptJournal.ReceiptTtl - TimeSpan.FromMinutes(1));

            Assert.That(journal.All(), Is.Empty);
        }
    }
}
